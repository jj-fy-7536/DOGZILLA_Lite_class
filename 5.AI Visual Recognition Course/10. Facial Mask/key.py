import RPi.GPIO as GPIO
import time,os
import subprocess

#set GPIO model
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

class Button:
    def __init__(self):
        self.keys = {
            "A": 24,
            "B": 23,
            "C": 17,
            "D": 22
        }
        self.setup_pins()

    def setup_pins(self):
      
        for pin in self.keys.values():
            os.system(f"sudo pinctrl set {pin} ip")

    def read_pin(self, pin):
      
        result = subprocess.run(["sudo", "pinctrl", "level", str(pin)], capture_output=True, text=True).stdout
        return result[0] == "1"

    def press_button(self, key_name):
        
        pin = self.keys.get(key_name)
        if pin is None:
            return False
        
        if self.read_pin(pin):
            return False

        # Wait until the button is released (pin reads '1')
        while not self.read_pin(pin):
            time.sleep(0.01)
        return True

    def press_a(self):
        return self.press_button("A")

    def press_b(self):
        if self.press_button("B"):
            os.system("pkill mplayer")
            return True
        return False

    def press_c(self):
        return self.press_button("C")

    def press_d(self):
        return self.press_button("D")

