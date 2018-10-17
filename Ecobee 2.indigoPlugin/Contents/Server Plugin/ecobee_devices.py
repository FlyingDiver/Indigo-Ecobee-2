#! /usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import json
import indigo
import temperature_scale
import logging
import ecobee

HVAC_MODE_MAP = {
    'heat'        : indigo.kHvacMode.Heat,
    'cool'        : indigo.kHvacMode.Cool,
    'auto'        : indigo.kHvacMode.HeatCool,
    'auxHeatOnly' : indigo.kHvacMode.Heat, # TODO: is this right?
    'off'         : indigo.kHvacMode.Off
}

FAN_MODE_MAP = {
    'auto': indigo.kFanMode.Auto,
    'on'  : indigo.kFanMode.AlwaysOn
}

class EcobeeBase:
    temperatureFormatter = temperature_scale.Fahrenheit()

    def __init__(self, dev, ecobee):
        self.logger = logging.getLogger('Plugin.ecobee_devices')
        
        self.dev = dev
        self.address = dev.pluginProps["address"]
        self.ecobee = ecobee
        self.name = self.address # temporary name until we get the real one from the server
                
        
    def get_capability(self, obj, cname):
        ret = None
        ret = [c for c in obj.get('capability') if cname == c.get('type')][0]
        return ret

    def updatable(self):
        if not self.dev.configured:
            self.logger.debug('device %s not fully configured yet; not updating state' % self.address)
            return False
        if not self.ecobee.authenticated:
            self.logger.info('not authenticated to Ecobee servers yet; not initializing state of device %s' % self.address)
            return False

        return True

    def _update_server_temperature(self, matchedSensor, stateKey):
        tempCapability = self.get_capability(matchedSensor, 'temperature')
        self.logger.debug('Sensor Temp: %s' % tempCapability.get('value'))
        return EcobeeBase.temperatureFormatter.report(self.dev, stateKey, tempCapability.get('value'))
        return temperature

    def _update_server_smart_temperature(self, ActualTemp, stateKey):
        return EcobeeBase.temperatureFormatter.report(self.dev, stateKey, ActualTemp)
        return temperature

    def _update_server_occupancy(self, matchedSensor):
        try:
            occupancyCapability = [c for c in matchedSensor.get('capability') if 'occupancy' == c.get('type')][0]
        except:
            return False
            
        occupied = ( 'true' == occupancyCapability.get('value') )
        self.dev.updateStateOnServer(key=u"occupied", value=occupied)
        return occupied

    def _update_server_fanMinOnTime(self, matchedSensor, stateKey, stateVal):
        self.dev.updateStateOnServer(stateKey, value=stateVal)

class EcobeeThermostat(EcobeeBase):
    ## This is for the Ecobee3 generation and later of products with occupancy detection and remote RF sensors

    def update(self):
        self.logger.debug("updating Ecobee3/4 thermostat from server")

        if not self.updatable():
            return

        thermostat = self.ecobee.get_thermostat(self.address)
        if not thermostat:
            self.logger.debug("update: no thermostat found for address {}".format(self.address))
            return

        self.logger.threaddebug("update: thermostat {} -\n{}".format(self.address, thermostat))
            
        runtime = thermostat.get('runtime')
        hsp = runtime.get('desiredHeat')
        csp = runtime.get('desiredCool')
        dispTemp = runtime.get('actualTemperature')
        climate = thermostat.get('program').get('currentClimateRef')

        settings = thermostat.get('settings')
        hvacMode = settings.get('hvacMode')
        fanMode = runtime.get('desiredFanMode')
        fanMinOnTime = settings.get('fanMinOnTime')

        status = thermostat.get('equipmentStatus')

        latestEventType = None
        if thermostat.get('events') and len(thermostat.get('events')) > 0:
            latestEventType = thermostat.get('events')[0].get('type')

        self.logger.debug('heat setpoint: %s, cool setpoint: %s, hvac mode: %s, fan mode: %s, climate: %s, status %s' % (hsp, csp, hvacMode, fanMode, climate, status))

        # should be exactly one; if not, we should panic
        matchedSensor = [
            rs for rs in thermostat['remoteSensors']
            if 'thermostat' == rs.get('type')
        ][0]

        self.logger.debug('matched sensor: {}'.format(matchedSensor))

        self.name = matchedSensor.get('name')

        self._update_server_smart_temperature(dispTemp, u'temperatureInput1')
        self._update_server_temperature(matchedSensor, u'temperatureInput2')
        self._update_server_fanMinOnTime(matchedSensor, u'fanMinOnTime', fanMinOnTime)
        self._update_server_occupancy(matchedSensor)

        # humidity
        humidityCapability = self.get_capability(matchedSensor, 'humidity')
        self.logger.debug('humidityCapability: {}'.format(humidityCapability))
        self.dev.updateStateOnServer(key="humidityInput1", value=float(humidityCapability.get('value')))

        EcobeeBase.temperatureFormatter.report(self.dev, "setpointHeat", hsp)
        EcobeeBase.temperatureFormatter.report(self.dev, "setpointCool", csp)
        self.dev.updateStateOnServer(key="hvacOperationMode", value=HVAC_MODE_MAP[hvacMode])
        self.dev.updateStateOnServer(key="hvacFanMode", value=FAN_MODE_MAP[fanMode])
        self.dev.updateStateOnServer(key="climate", value=climate)

        self.dev.updateStateOnServer(key="hvacHeaterIsOn", value=bool(status and ('heatPump' in status or 'auxHeat' in status)))
        self.dev.updateStateOnServer(key="hvacCoolerIsOn", value=bool(status and ('compCool' in status)))
        self.dev.updateStateOnServer(key="hvacFanIsOn", value=bool(status and ('fan' in status or 'ventilator' in status)))

        self.dev.updateStateOnServer(key="autoHome", value=bool(latestEventType and ('autoHome' in latestEventType)))
        self.dev.updateStateOnServer(key="autoAway", value=bool(latestEventType and ('autoAway' in latestEventType)))


class EcobeeSmartThermostat(EcobeeBase):
    ## This is the older 'Smart' and 'Smart Si' prior to Ecobee3

    def update(self):
        self.logger.debug("updating Ecobee Smart/Si thermostat from server")

        if not self.updatable():
            return

        thermostat = self.ecobee.get_thermostat(self.address)
        if not thermostat:
            return
            
        runtime = thermostat.get('runtime')
        hsp = runtime.get('desiredHeat')
        csp = runtime.get('desiredCool')
        temp = runtime.get('actualTemperature')
        hum = runtime.get('actualHumidity')
        climate = thermostat.get('program').get('currentClimateRef')

        settings = thermostat.get('settings')
        hvacMode = settings.get('hvacMode')
        fanMode = runtime.get('desiredFanMode')

        status = thermostat.get('equipmentStatus')

        self.logger.debug('heat setpoint: %s, cool setpoint: %s, hvac mode: %s, fan mode: %s, climate: %s, status %s' % (hsp, csp, hvacMode, fanMode, climate, status))

        self.name = thermostat.get('name')

        self._update_server_smart_temperature(temp, u'temperatureInput1')

        # humidity
        self.dev.updateStateOnServer(key="humidityInput1", value=float(hum))

        EcobeeBase.temperatureFormatter.report(self.dev, "setpointHeat", hsp)
        EcobeeBase.temperatureFormatter.report(self.dev, "setpointCool", csp)
        self.dev.updateStateOnServer(key="hvacOperationMode", value=HVAC_MODE_MAP[hvacMode])
        self.dev.updateStateOnServer(key="hvacFanMode", value=FAN_MODE_MAP[fanMode])
        self.dev.updateStateOnServer(key="climate", value=climate)

        self.dev.updateStateOnServer(key="hvacHeaterIsOn", value=bool(status and ('heatPump' in status or 'auxHeat' in status)))
        self.dev.updateStateOnServer(key="hvacCoolerIsOn", value=bool(status and ('compCool' in status)))
        self.dev.updateStateOnServer(key="hvacFanIsOn", value=bool(status and ('fan' in status or 'ventilator' in status)))


class EcobeeRemoteSensor(EcobeeBase):

    def update(self):
        self.logger.debug("updating Ecobee Remote Sensor from server")

        if not self.updatable():
            return

        matchedSensor = self.ecobee.get_remote_sensor(self.address)

        self.name = matchedSensor.get('name')

        try:
            self._update_server_temperature(matchedSensor, u'sensorValue')
        except ValueError:
            self.logger.error("%s: couldn't format temperature value; is the sensor alive?" % self.name)


        # if occupancy was detected, set the icon to show a 'tripped' motion sensor;
        # otherwise, just show the thermometer for the temperature sensor
        if self._update_server_occupancy(matchedSensor):
            self.dev.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
        else:
            self.dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensor)

