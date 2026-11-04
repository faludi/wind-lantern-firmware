import time


RED_PIN = 5
GREEN_PIN = 6
BLUE_PIN = 7
RED_PIN_2 = 8
GREEN_PIN_2 = 9
BLUE_PIN_2 = 10
SETUP_BUTTON_PIN = 16


class Pulse:
    def __init__(self, pin, pwm_factory):
        self._pwm = pwm_factory(pin)
        self._pwm.freq(300)
        self.duty(100)

    def duty(self, percent_duty):
        """Set PWM duty cycle from a 0-100 percentage."""
        return self._pwm.duty_u16(int(percent_duty / 100 * 65535))


class LanternHardware:
    def __init__(self, pin_factory=None, pwm_factory=None, sleep_ms=None):
        if pin_factory is None or pwm_factory is None:
            from machine import Pin, PWM
            if pin_factory is None:
                pin_factory = Pin
            if pwm_factory is None:
                pwm_factory = PWM

        self._pin_factory = pin_factory
        self._sleep_ms = sleep_ms
        self.red_pwm = Pulse(pin_factory(RED_PIN), pwm_factory)
        self.green_pwm = Pulse(pin_factory(GREEN_PIN), pwm_factory)
        self.red_pwm_2 = Pulse(pin_factory(RED_PIN_2), pwm_factory)
        self.green_pwm_2 = Pulse(pin_factory(GREEN_PIN_2), pwm_factory)
        self.blue = pin_factory(BLUE_PIN, pin_factory.OUT)
        self.blue_2 = pin_factory(BLUE_PIN_2, pin_factory.OUT)
        self.blue.value(1)
        self.blue_2.value(1)

    def set_primary_duties(self, red, green):
        self.red_pwm.duty(red)
        self.green_pwm.duty(green)

    def set_secondary_duties(self, red, green):
        self.red_pwm_2.duty(red)
        self.green_pwm_2.duty(green)

    def set_red_duties(self, primary, secondary):
        self.red_pwm.duty(primary)
        self.red_pwm_2.duty(secondary)

    def set_green_duties(self, primary, secondary):
        self.green_pwm.duty(primary)
        self.green_pwm_2.duty(secondary)

    def set_candle_duties(self, red, green, red_2, green_2):
        self.set_primary_duties(red, green)
        self.set_secondary_duties(red_2, green_2)

    def set_status_color(self, red, green):
        self.set_candle_duties(100 - red, 100 - green, 100 - red, 100 - green)

    def setup_button_pressed(self):
        """Return whether the active-low setup button is held, after debounce."""
        button = self._pin_factory(SETUP_BUTTON_PIN, self._pin_factory.IN, self._pin_factory.PULL_UP)
        if button.value():
            return False
        if self._sleep_ms is None:
            time.sleep_ms(50)
        else:
            self._sleep_ms(50)
        return not button.value()