from machine import Pin, Timer,

def singleton(class_):
    instances = {}
    def getinstance(*args, **kwargs):
        if class_ not in instances:
            instances[class_] = class_(*args, **kwargs)
        return instances[class_]
    return getinstance


@singleton
class LED:
    
    def __init__(self):
        self.led = Pin("LED", Pin.OUT)
        self.timer = Timer()

    def on(self):
        self.timer.deinit()
        self.led.high()
        
    def off(self):
        self.timer.deinit()
        self.led.low()

    def blink(self,f):
         self.timer.init(freq=f, mode=Timer.PERIODIC, callback=lambda t: self.led.toggle())
    
    def disable(self):
        self.off()
        self.timer.deinit()

    def blinkfor(self,f,duration):
         self.timer.init(freq=f, mode=Timer.PERIODIC, callback=lambda t: self.led.toggle())
         sleep(duration)
         self.disable()
         
    def __del__(self):
        self.disable()


