import tk

from tkinter import *
window = Tk()

screen_w = window.winfo_screenwidth()
screen_h = window.winfo_screenheight()
center_w = int(screen_w/2 - 500/2)
center_h = int(screen_h/2 - 500/2)

def when_clicked():
    print("Button Pressed!")
    #do the enter function
    pass

btn = Button(window, command=when_clicked,text="Next", fg='blue')
btn.place(x=200, y=100)

lbl = Label(window, text="Press Button to place next piece", fg='black', font=("Helvetica", 16))

lbl.place(x=80, y=50)


window.title('Robot Controller')
window.geometry(f'500x500+{center_w}+{center_h}')
#window.mainloop()
while True:
    window.update_idletasks()
    window.update()
