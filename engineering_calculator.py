import tkinter as tk
from tkinter import ttk

class EngineeringCalculator:
    def __init__(self, root):
        self.root = root
        self.root.title("Engineering Calculator")
        self.root.geometry("400x600")
        
        self.display_var = tk.StringVar()
        self.display_var.set("0")
        
        self.create_widgets()
    
    def create_widgets(self):
        # Display
        display = ttk.Entry(self.root, textvariable=self.display_var, font=('Arial', 24), justify='right')
        display.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=10, pady=10)
        
        # Buttons
        buttons = [
            ('7', 1, 0), ('8', 1, 1), ('9', 1, 2), ('/', 1, 3),
            ('4', 2, 0), ('5', 2, 1), ('6', 2, 2), ('*', 2, 3),
            ('1', 3, 0), ('2', 3, 1), ('3', 3, 2), ('-', 3, 3),
            ('0', 4, 0), ('.', 4, 1), ('=', 4, 2), ('+', 4, 3),
            ('sin', 5, 0), ('cos', 5, 1), ('tan', 5, 2), ('√', 5, 3),
            ('log', 6, 0), ('ln', 6, 1), ('π', 6, 2), ('C', 6, 3)
        ]
        
        for (text, row, col) in buttons:
            btn = ttk.Button(self.root, text=text, command=lambda t=text: self.on_button_click(t))
            btn.grid(row=row, column=col, sticky="nsew", padx=5, pady=5)
        
        # Configure grid weights
        for i in range(7):
            self.root.grid_rowconfigure(i, weight=1)
        for i in range(4):
            self.root.grid_columnconfigure(i, weight=1)
    
    def on_button_click(self, button_text):
        current = self.display_var.get()
        
        if button_text == 'C':
            self.display_var.set("0")
        elif button_text == '=':
            try:
                result = eval(current)
                self.display_var.set(str(result))
            except:
                self.display_var.set("Error")
        elif button_text == '√':
            self.display_var.set(str(eval(f"math.sqrt({current})")))
        elif button_text == 'π':
            self.display_var.set(str(math.pi))
        elif button_text in ['sin', 'cos', 'tan', 'log', 'ln']:
            self.display_var.set(str(eval(f"math.{button_text}({current})")))
        else:
            if current == "0" or current == "Error":
                self.display_var.set(button_text)
            else:
                self.display_var.set(current + button_text)

if __name__ == "__main__":
    import math
    root = tk.Tk()
    app = EngineeringCalculator(root)
    root.mainloop()