from machine import Pin
class Valve:
    class State:
        OPEN = 1
        CLOSED = 0

    def __init__(self,pin):
        self.pin = pin
        self.relay = Pin(pin, Pin.OUT)
        self.state = Valve.State.CLOSED
        self.closeValve()
    
    def openValve(self):
            self.relay.high()
            #print(f"Opened {self.pin}")
            self.state = Valve.State.OPEN
        
    def closeValve(self):
            self.relay.low()
            #print(f"Closed {self.pin}")
            self.state = Valve.State.CLOSED

    def setState(self,newstate):
        if (newstate == Valve.State.CLOSED):
            self.closeValve()
        if (newstate == Valve.State.OPEN):
            self.openValve()

    def getState(self):
        #state = Valve.State.OPEN if self.state == Valve.State.CLOSED else "Close"
        #print(f"{self.pin} {state}")
        return self.state