#! /usr/bin/env python
# -*- coding: utf-8 -*-

FORMAT_STRING = "{0:.1f}"

class TemperatureScale:

    def report(self, dev, stateKey, reading):
        dev.updateStateOnServer(key=stateKey, value=self.convertFromEcobee(reading), decimalPlaces=1, uiValue=self.format(reading))
        return txt

    def format(self, reading):
        return u"%s%s" % (FORMAT_STRING.format(self.convertFromEcobee(reading)), self.suffix())


class Fahrenheit(TemperatureScale):

    # convertFromEcobee() methods input the Ecobee temperature value (F x 10) and output the converted value for the class
    def convertFromEcobee(self, temp):
        return float(temp) / 10.0
        
    # convertToEcobee() methods input the temperature value in the current scale and output the Ecobee value int(F x 10)
    def convertToEcobee(self, temp):
        return int(temp * 10)
        
    def suffix(self):
        return u"°F"
        
class Celsius(TemperatureScale):

    def convertFromEcobee(self, reading):
        return ((float(reading) / 10.0) - 32.0) * 5.0 / 9.0
        
    def convertToEcobee(self, temp):
        return int((9.0 * temp)/5.0 + 32.0) * 10
        
    def suffix(self):
        return u"°C"
        
class Kelvin(TemperatureScale):

    def convertFromEcobee(self, reading):
        return (((float(reading) / 10.0) - 32.0) * 5.0 / 9.0) + 273.15
        
    def convertToEcobee(self, temp):
        return int((9.0 * temp)/5.0 - 459.67) * 10
        
    def suffix(self):
        return u"K"
        
class Rankine(TemperatureScale):

    def convertFromEcobee(self, reading):
        return (float(reading) / 10.0) + 459.67
        
    def convertToEcobee(self, temp):
        return int(temp - 459.67) * 10
        
    def suffix(self):
        return u"°Ra"
        
