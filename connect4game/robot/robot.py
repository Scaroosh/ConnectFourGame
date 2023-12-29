import threading
import time
from contextlib import contextmanager

from enums import GripperAction
from pyniryo2 import ConveyorDirection, NiryoRobot, PoseObject, ToolID
from roslibpy.core import RosTimeoutError
from connect4game.utils.logging import create_logger


class Robot:
    # Constant variables

    # Logger for the robot
    __logger = create_logger(
        name="ROBOT"
    )

    # Positions of certain objects

    # Better pos for robot when it should stay idle, got the pos from NiryoStudio
    __better_home_pos = PoseObject(
        x=0.14, y=0, z=0.203,
        roll=0, pitch=0.759, yaw=0
    )

    __mag_pos_bef = PoseObject(
        x=0.107, y=-0.248, z=0.21,
        roll=-2.947, pitch=1.242, yaw=-2.934
    )

    # 15.9 - 16.0 cm from first speaker hole
    __mag_pos = PoseObject(
        x=0.091, y=-0.254, z=0.161,
        roll=3.125, pitch=1.186, yaw=3.1
    )

    # Location of the piece at index 0 on the belt (robot side)
    __index0_pos = PoseObject(
        x=0.125, y=0.0, z=0.152,
        roll=0.0, pitch=1.55, yaw=0
    )

    # Location of the piece at index 1 on the belt (far side)
    __index1_pos = PoseObject(
        x=0.186, y=0.0, z=0.15,
        roll=0.0, pitch=1.55, yaw=0
    )

    # Board positions where the robot can drop the pieces to the lanes
    # Starting from 0 most right lane for the robot
    __board_pos = (
        (
            PoseObject(
                x=0.168, y=0.314, z=0.223,
                roll=-0.06, pitch=-0.016, yaw=1.551
            ),
            PoseObject(
                x=0.164, y=0.316, z=0.208,
                roll=0.063, pitch=-0.006, yaw=1.546
            )
        ),
    )

    def __init__(self, robot_ip = "169.254.200.200"): # if ip addr is argument not provided then use the ethernet port
        # Connect to robot
        try:
            self.__robot = NiryoRobot(robot_ip)
        except:
            self.__logger.critical("Robot connection failed, check if the ip addr is correct")
            raise
        
        try:
            # Calibrate the robot
            self.__arm = self.__robot.arm
            self.__execute_robot_action(
                self.__arm.calibrate_auto
            )
            self.__logger.info("Arm is calibrated and ready to use")

            # Detect the currently attached tool
            self.__tool = self.__robot.tool
            self.__execute_robot_action(
                self.__tool.update_tool
            )
            current_tool_id = self.__execute_robot_action(
                self.__tool.get_current_tool_id
            )
            if current_tool_id not in [ToolID.GRIPPER_1, ToolID.GRIPPER_2, ToolID.GRIPPER_3, ToolID.GRIPPER_4]:
                self.__logger.error("Gripper not detected! Ignore this if running in simulation mode")
            # Open the gripper
            self.__control_gripper(GripperAction.OPEN)
            self.__logger.info("Gripper is ready to use")

            # Set up the conveyer belt
            self.__conveyor = self.__robot.conveyor
            self.__conveyor_id = self.__execute_robot_action(
                self.__conveyor.set_conveyor
            )
            self.__logger.info("Conveyer belt ready to use")

            # Move robot to its default position
            self.__move_to_home()
        except:
            # If any error happens during the calibration, end the robot so the code does not hang
            self.end_robot()
            raise
        
        # A lock to use the belt
        self.__belt_lock = threading.Lock()

        # Total number of pieces currently on the belt
        self.__current_piece_count = 0

        # Piece of left on the current stack can be (0, 1, 2)
        self.__current_stack_count = 0

        self.__board_calibrated = False

        self.__magazine_ready = False

    # Returns the current total left pieces on the belt
    @property
    def get_piece_count(self):
        return self.__current_piece_count
    
    # Returns the current count of piece left on the stack
    @property
    def get_stack_count(self):
        return self.__current_stack_count

    @property
    def get_board_status(self):
        return self.__board_calibrated

    @property
    def get_magazine_status(self):
        return self.__magazine_ready
    
    # Belt set up function to place the piece on the belt before the game starts
    def set_up_game(self, piece_count=21):
        # Set up magazine if not already set up
        if not self.__magazine_ready:
            self.__move_to_pos(self.__mag_pos_bef)
            self.__move_to_pos(self.__mag_pos)

            self.__logger.info("Magazine should be placed to the shown position by the robot")
            self.__logger.info("Press enter to continue after the magazine is ready")
            input()

            self.__move_to_home()

            self.__magazine_ready = True

        while self.__current_piece_count != piece_count:
            self.__logger.info(f"Currently setting up piece {self.__current_piece_count}")
            
            # Determine which place to show to the user
            self.__logger.info(f"This piece will be placed on position {self.__current_stack_count + 1}")
            self.__current_piece_count += 1

            # Move to magazine to grab a piece
            self.__move_to_pos(self.__mag_pos_bef)

            # Decrease arm speed for precise actions
            with self.__slow_arm_control():
                self.__move_to_pos(self.__mag_pos)
            
                self.__control_gripper(GripperAction.CLOSE)

                self.__move_to_pos(self.__mag_pos_bef)

            self.__move_to_home()

            if not self.__board_calibrated:
                self.__calibrate_board()

            # Acquire belt control to place the piece
            with self.__belt_lock:
                self.__logger.info("Belt locked for placing the piece")
                self.__move_to_pos(self.__index0_pos if self.__current_stack_count==0 else self.__index1_pos)
                self.__current_stack_count += 1

                self.__control_gripper(GripperAction.OPEN)

                # Move to home after piece is placed
                self.__move_to_home()

            # Move the belt since 2 pieces were placed
            if self.__current_stack_count == 2 and (self.__current_piece_count != piece_count):
                self.__logger.info("Piece stack full moving pieces to the left")
                self.__current_stack_count = 0
                belt_action = threading.Thread(target=self.__move_pieces_on_belt, args=(ConveyorDirection.BACKWARD,))
                belt_action.start()
    
    # Grab the next piece, which piece to grab is calculated by itself
    def grab_piece(self):
        # If there are no pieces left on the belt don't do anything
        if self.__current_piece_count == 0:
            return
        
        # Make sure belt does not move while taking the pieces
        with self.__belt_lock:
            self.__move_to_pos(self.__index0_pos if self.__current_stack_count==1 else self.__index1_pos)
            self.__control_gripper(GripperAction.CLOSE)
            self.__move_to_home()
            self.__current_stack_count -= 1
            self.__current_piece_count -= 1
        
        # If stack is empty then move the new stones on the belt async
        if self.__current_stack_count == 0 and self.__current_piece_count != 0:
            self.__current_stack_count = 2
            belt_thread = threading.Thread(target=self.__move_pieces_on_belt, args=(ConveyorDirection.FORWARD,))
            belt_thread.start()

    # Drop the piece to the specified lane starting from 0
    def drop_piece_to_board(self, index):
        self.__move_to_pos(self.__board_pos[index][0])
        self.__move_to_pos(self.__board_pos[index][1])

        self.__control_gripper(GripperAction.OPEN)

        self.__move_to_pos(self.__board_pos[index][0])
        self.__move_to_home()

    # Function to end the control instance, must be called at the end
    def end_robot(self):
        self.__robot.end()

    # Move pieces on the belt
    def __move_pieces_on_belt(self, direction: ConveyorDirection):
        with self.__belt_lock:
            self.__logger.info(f"Belt locked by thread {threading.get_ident()}")
            log_dirct = "left" if ConveyorDirection.BACKWARD else "right"
            self.__logger.info(f"Conveyor belt is currently moving to the {log_dirct}")

            self.__execute_robot_action(
                self.__conveyor.run_conveyor, self.__conveyor_id, 25, direction
            )
            time.sleep(2.5)
            self.__execute_robot_action(
                self.__conveyor.stop_conveyor, self.__conveyor_id
            )

            self.__logger.info(f"Belt locked removed by thread {threading.get_ident()}")

    # work around ros timing bug where the robot fails sometimes for no reason
    def __execute_robot_action(self, action, *args):
        action_retry = True
        result = None
        try:
            name = action.__name__
        except AttributeError:
            name = None
        
        while action_retry:
            try:
                result = action(*args)
                action_retry = False
            except TypeError:
                self.__logger.critical("You did a coding error, do not pass function call instead pass a function reference")
                raise
            except RosTimeoutError:
                # robot internal bug safe to ignore
                # TODO in the future maybe add a retry limit
                three_dot = "..."
                self.__logger.warning(f"Robot internal timing bug, safe to ignore, retrying action {three_dot if name is None else name}")
                continue
        return result

    # Function for moving back to the home pose
    def __move_to_home(self):
        self.__move_to_pos(self.__better_home_pos)
        self.__logger.info("Moved to home position")

    # Move robot to specified position
    def __move_to_pos(self, pos: PoseObject):
        self.__execute_robot_action(
            self.__arm.move_pose, pos
        )
        self.__logger.info(f"Moved to position x={pos.x}, y={pos.y}, z={pos.z}, roll={pos.roll}, pitch={pos.pitch}, yaw={pos.yaw}")

    def __control_gripper(self, action: GripperAction):
        try:
            tool_state = self.__arm.hardware_status.value.hardware_errors[7]

            # If tool has an error, reboot the tool until error state clears
            # I know this looks stupid but this is how it works in NiryoStudio
            while tool_state != 0:
                self.__logger.warning("Gripper overheadted or run into an error, restarting gripper")
                self.__execute_robot_action(
                    self.__tool.update_tool
                )
                tool_state = self.__arm.hardware_status.value.hardware_errors[7]
        except IndexError:
            self.__logger.info("Skipping hardware status checking for simulation since it does not exist")
        
        if action == GripperAction.OPEN:
            self.__execute_robot_action(
                    self.__tool.release_with_tool
            )
        else:
            self.__execute_robot_action(
                    self.__tool.grasp_with_tool
            )

    # This function calibrates the place of the game board
    def __calibrate_board(self):
        # Calibrate board positions
        for board_row in self.__board_pos:
            self.__move_to_pos(board_row[0])
            with self.__slow_arm_control():
                self.__move_to_pos(board_row[1])

                # Wait for user confirmation
                self.__logger.info("Waiting for you to adjust the game board, press enter to continue...")
                input()

                self.__move_to_pos(board_row[0])

        self.__move_to_home()
        self.__board_calibrated = True

    @contextmanager
    def __slow_arm_control(self, slow_speed=30):
        slow_arm = self.__arm
        self.__execute_robot_action(
            slow_arm.set_arm_max_velocity, slow_speed
        )
        try:
            yield slow_arm
        finally:
            self.__execute_robot_action(
                slow_arm.set_arm_max_velocity, 100
            )

# TODO Implement unittest later
# Test function for robot
def __robotTest(args):
    test_logger = create_logger("ROBOT_TEST")
    
    try:
        robot_ethernet = Robot(args.ip)
    except:
        test_logger.exception("Robot init failed!")
        raise

    try:
        original_piece = args.piece
        robot_ethernet.set_up_game(piece_count=args.piece)
        while original_piece:
            original_piece -= 1
            robot_ethernet.grab_piece()
            robot_ethernet.drop_piece_to_board(0)
        
    except KeyboardInterrupt:
        test_logger.info("Program ended with keyboard interrupt")
        sys.exit(130)
    finally:
        robot_ethernet.end_robot()

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run tests for the robot")

    # Options ip argument for connecting to other robots, such as the simulation
    parser.add_argument(
        "--ip", 
        type=str,
        help="ip addr for robot, defaults to ethernet ip",
        default="169.254.200.200"
    )

    parser.add_argument(
        "--piece",
        type=int,
        help="game piece count",
        default=21
    )

    args = parser.parse_args()

    __robotTest(args)