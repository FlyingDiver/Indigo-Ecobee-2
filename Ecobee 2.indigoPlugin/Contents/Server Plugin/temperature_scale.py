#! /usr/bin/env python
# -*- coding: utf-8 -*-

FORMAT_STRING = "{0:.1f}"

class TemperatureScale:

    def report(self, dev, stateKey, reading):
        dev.updateStateOnServer(key=stateKey, value=self.convertFromEcobee(reading), decimalPlaces=1, uiValue=self.format(reading))
        return txt

    def format(self, reading):
        return f"{FORMAT_STRING.format(self.convertFromEcobee(reading))}{self.suffix()}"


class Fahrenheit(TemperatureScale):

    # convertFromEcobee() methods input the Ecobee temperature value (F x 10) and output the converted value for the class
    @staticmethod
    def convertFromEcobee(temp):
        return float(temp) / 10.0
        
    # convertToEcobee() methods input the temperature value in the current scale and output the Ecobee value int(F x 10)
    @staticmethod
    def convertToEcobee(temp):
        return int(temp * 10)
        
    @staticmethod
    def suffix():
        return "°F"
        
class Celsius(TemperatureScale):

    @staticmethod
    def convertFromEcobee(reading):
        return ((float(reading) / 10.0) - 32.0) * 5.0 / 9.0
        
    @staticmethod
    def convertToEcobee(temp):
        return int((9.0 * temp)/5.0 + 32.0) * 10
        
    @staticmethod
    def suffix():
        return "°C"
        
class Kelvin(TemperatureScale):

    @staticmethod
    def convertFromEcobee(reading):
        return (((float(reading) / 10.0) - 32.0) * 5.0 / 9.0) + 273.15
        
    @staticmethod
    def convertToEcobee(temp):
        return int((9.0 * temp)/5.0 - 459.67) * 10
        
    @staticmethod
    def suffix():
        return u"K"
        
class Rankine(TemperatureScale):

    @staticmethod
    def convertFromEcobee(reading):
        return (float(reading) / 10.0) + 459.67
        
    @staticmethod
    def convertToEcobee(temp):
        return int(temp - 459.67) * 10
        
    @staticmethod
    def suffix():
        return "°Ra"
        
