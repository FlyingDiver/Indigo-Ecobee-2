#! /usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import json
import time
import logging

import temperature_scale
import indigo
import ecobee

#
# All interactions with the Ecobee servers are encapsulated in this class
#

class EcobeeAccount:

    def __init__(self, dev, api_key, refresh_token = None):
        self.logger = logging.getLogger("Plugin.EcobeeAccount")
        self.dev = dev
        self.api_key = api_key

        self.serverData = None
        self.authenticated = False
        self.next_refresh = time.time()
        
        
        if refresh_token:
            self.logger.debug("EcobeeAccount __init__, using refresh token = {}".format(refresh_token))
            self.refresh_token = refresh_token
            self.do_token_refresh()
            
        if self.authenticated:
            self.server_update()
        
#
#   Data fetching functions
#

    def get_thermostats(self):
        return self.serverData

    def get_thermostat(self, address):
        ths = self.get_thermostats()
        if not ths:
            return None
        
        return [
            th for th in ths
            if address == th.get('identifier')
        ][0]

    def get_remote_sensors(self):
        return [
            rs
            for th in self.get_thermostats()
            for rs in th['remoteSensors']
                if ('ecobee3_remote_sensor' == rs.get('type'))
        ]

    def get_remote_sensor(self, address):
        rss = self.get_remote_sensors()
        self.logger.threaddebug("looking for remote sensor %s in %s" % (address, rss))
        return [
            rs for rs in rss
            if address == rs.get('code')
        ][0]

#
#   Ecobee Authentication functions
#

    # Authentication Step 1
    def request_pin(self):
        
        params = {'response_type': 'ecobeePin', 'client_id': self.api_key, 'scope': 'smartWrite'}
        try:
            request = requests.get('https://api.ecobee.com/authorize', params=params)
        except requests.RequestException, e:
            self.logger.error("PIN Request Error, exception = {}".format(e))
            return None
            
        if request.status_code == requests.codes.ok:
            self.authorization_code = request.json()['code']
            pin = request.json()['ecobeePin']
            self.logger.debug("PIN Request OK, pin = {}. authorization_code = {}".format(pin, self.authorization_code))
            return pin
            
        else:
            self.logger.error("PIN Request failed, response = '{}'".format(request.text))                
            return None

    # Authentication Step 3
    def get_tokens(self):
    
        params = {'grant_type': 'ecobeePin', 'code': self.authorization_code, 'client_id': self.api_key}
        try:
            request = requests.post('https://api.ecobee.com/token', params=params)
        except requests.RequestException, e:
            self.logger.error("Token Request Error, exception = {}".format(e))
            self.authenticated = False
            return
            
        if request.status_code == requests.codes.ok:
            self.access_token = request.json()['access_token']
            self.refresh_token = request.json()['refresh_token']
            expires_in = request.json()['expires_in']
            self.logger.debug("Token Request OK, access_token = {}, refresh_token = {}, expires_in = {}".format(self.access_token, self.refresh_token, expires_in))
            self.next_refresh = time.time() + (float(expires_in) * 0.80)
            self.authenticated = True
        else:
            self.logger.error("Token Request failed, response = '{}'".format(request.text))                
            self.authenticated = False


    # called from __init__ or main loop to refresh the access tokens

    def do_token_refresh(self):
        if not self.refresh_token:
            self.authenticated = False
            return
            
        self.logger.debug("Token Request with refresh_token = {}".format(self.refresh_token))

        params = {'grant_type': 'refresh_token', 'refresh_token': self.refresh_token, 'client_id': self.api_key}
        try:
            request = requests.post('https://api.ecobee.com/token', params=params)
        except requests.RequestException, e:
            self.logger.error("Token Refresh Error, exception = {}".format(e))
            return
            
        if request.status_code == requests.codes.ok:
            self.access_token = request.json()['access_token']
            self.refresh_token = request.json()['refresh_token']
            expires_in = request.json()['expires_in']
            self.logger.debug("Token Refresh OK, access_token = {}, refresh_token = {}, expires_in = {}".format(self.access_token, self.refresh_token, expires_in))
            self.next_refresh = time.time() + (float(expires_in) * 0.80)
            self.authenticated = True
            
        else:
            self.logger.error("Token Refresh failed, response = '{}'".format(request.text))                
            self.next_refresh = time.time() + 300.0         # try every five minutes
            self.authenticated = False

        
#
#   Ecobee API functions
#
        
#   Request all thermostat data from the Ecobee servers.

    def server_update(self):
    
        header = {'Content-Type': 'application/json;charset=UTF-8',
                  'Authorization': 'Bearer ' + self.access_token}
        params = {'json': ('{"selection":{"selectionType":"registered",'
                            '"includeRuntime":"true",'
                            '"includeSensors":"true",'
                            '"includeProgram":"true",'
                            '"includeEquipmentStatus":"true",'
                            '"includeEvents":"true",'
                            '"includeWeather":"true",'
                            '"includeSettings":"true"}}')}
        try:
            request = requests.get('https://api.ecobee.com/1/thermostat', headers=header, params=params)
        except requests.RequestException, e:
            self.logger.error("Thermostat Update Error, exception = {}".format(e))
            return
            
        if request.status_code == requests.codes.ok:
            self.serverData = request.json()['thermostatList']
            self.logger.debug("Thermostat Update OK, got info on {} devices".format(len(self.serverData)))
        else:
            self.logger.error("Thermostat Update failed, response = '{}'".format(request.text))                


#   Generic routine for other API calls

    def make_request(self, body, log_msg_action):
        url = 'https://api.ecobee.com/1/thermostat'
        header = {'Content-Type': 'application/json;charset=UTF-8',
                 'Authorization': 'Bearer ' + self.access_token}
        params = {'format': 'json'}
        try:
            request = requests.post(url, headers=header, params=params, json=body)
        except RequestException:
            self.logger.error("API Error connecting to Ecobee.  Possible connectivity outage. Could not make request: {}".format(log_msg_action))
            return None
            
        if not request.status_code == requests.codes.ok:
            self.logger.debug("API '{}' request failed, result = {}".format(log_msg_action, request.text))
            return None

        self.logger.debug("API '{}' request completed, result = {}".format(log_msg_action, request))
        return request



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

    def __init__(self, dev):
        self.logger = logging.getLogger('Plugin.ecobee_devices')
        
        self.dev = dev
        self.address = dev.pluginProps["address"]
        self.ecobee = None
                
        
    def updatable(self):
        if not self.dev.configured:
            self.logger.debug('device %s not fully configured yet; not updating state' % self.address)
            return False
            
        # has the Ecobee account been initialized yet?
        if not self.ecobee:
            try:
                accountID = int(self.dev.pluginProps["account"])
                self.ecobee = indigo.activePlugin.ecobee_accounts[accountID]
            except:
                self.logger.error(u"updatable: Error obtaining ecobee account object")
                return False
            
            if not self.ecobee.authenticated:
                self.logger.info('not authenticated to Ecobee servers yet; not initializing state of device %s' % self.address)
                return False

        return True

    def get_capability(self, obj, cname):
        ret = None
        ret = [c for c in obj.get('capability') if cname == c.get('type')][0]
        return ret

    def _update_server_temperature(self, matchedSensor, stateKey):
        tempCapability = self.get_capability(matchedSensor, 'temperature')
        return EcobeeBase.temperatureFormatter.report(self.dev, stateKey, tempCapability.get('value'))

    def _update_server_smart_temperature(self, ActualTemp, stateKey):
        return EcobeeBase.temperatureFormatter.report(self.dev, stateKey, ActualTemp)

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

    def set_hvac_mode(self, hvac_mode):     # possible hvac modes are auto, auxHeatOnly, cool, heat, off 
        body =  {
                    "selection": 
                    {
                        "selectionType"  : "thermostats",
                        "selectionMatch" : self.dev.address 
                    },
                    "thermostat" : 
                    {
                        "settings": 
                        {
                            "hvacMode": hvac_mode
                        }
                    }
                }
        log_msg_action = "set HVAC mode"
        return self.ecobee.make_request(body, log_msg_action)


    def set_hold_temp(self, cool_temp, heat_temp, hold_type="nextTransition"):  # Set a hold
        body =  {
                    "selection": 
                    {
                        "selectionType"  : "thermostats",
                        "selectionMatch" : self.dev.address 
                    },
                    "functions": 
                    [
                        {
                            "type"   : "setHold", 
                            "params" : 
                            {
                                "holdType": hold_type,
                                "coolHoldTemp": int(cool_temp * 10),
                                "heatHoldTemp": int(heat_temp * 10)
                            }
                        }
                    ]
                }
        log_msg_action = "set hold temp"
        return self.ecobee.make_request(body, log_msg_action)

    def set_hold_temp_with_fan(self, cool_temp, heat_temp, hold_type="nextTransition"):     # Set a fan hold
        body =  {
                    "selection" : 
                    {
                        "selectionType"  : "thermostats",
                        "selectionMatch" : self.dev.address 
                    },
                    "functions" : 
                    [
                        {
                            "type"   : "setHold", 
                            "params" : 
                            {
                                "holdType"     : hold_type,
                                "coolHoldTemp" : int(cool_temp * 10),
                                "heatHoldTemp" : int(heat_temp * 10),
                                "fan"          : "on"
                            }
                        }
                    ]
                }
        log_msg_action = "set hold temp with fan on"
        return self.ecobee.make_request(body, log_msg_action)

    def set_climate_hold(self, climate, hold_type="nextTransition"):    # Set a climate hold - ie away, home, sleep
        body =  {
                    "selection" : 
                    {
                        "selectionType"  : "thermostats",
                        "selectionMatch" : self.dev.address 
                    },
                    "functions" : 
                    [
                        {
                            "type"   : "setHold", 
                            "params" : 
                            {
                                "holdType"       : hold_type,
                                "holdClimateRef" : climate
                            }
                        }
                    ]
                }
        log_msg_action = "set climate hold"
        return self.ecobee.make_request(body, log_msg_action)

    def resume_program(self):   # Resume currently scheduled program
        body =  {
                    "selection" : 
                    {
                        "selectionType"  : "thermostats",
                        "selectionMatch" : self.dev.address 
                    },
                    "functions" : 
                    [
                        {
                            "type"   : "resumeProgram", 
                            "params" : 
                            {
                                "resumeAll": "False"
                            }
                        }
                    ]
                }
        log_msg_action = "resume program"
        return self.ecobee.make_request(body, log_msg_action)



    def get_climates(self):
        thermostat = self.ecobee.get_thermostat(self.address)
        return [
            (rs.get('climateRef'), rs.get('name'))
            for rs in thermostat.get('program').get('climates')
        ]



## This is for the Ecobee3 generation and later of products with occupancy detection and remote RF sensors

class EcobeeThermostat(EcobeeBase):

    def update(self):

        if not self.updatable():
            return

        thermostat = self.ecobee.get_thermostat(self.address)
        if not thermostat:
            self.logger.debug("update: no thermostat found for address {}".format(self.address))
            return

        self.logger.threaddebug("update: thermostat {} -\n{}".format(self.address, thermostat))
            
        runtime = thermostat.get('runtime')
        modelNumber = thermostat.get('modelNumber')
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
        self._update_server_occupancy(matchedSensor)

        self.dev.updateStateOnServer(key="fanMinOnTime", value=fanMinOnTime)

        # humidity
        humidityCapability = self.get_capability(matchedSensor, 'humidity')
        self.logger.debug('humidityCapability: {}'.format(humidityCapability))
        self.dev.updateStateOnServer(key="humidityInput1", value=float(humidityCapability.get('value')))

        EcobeeBase.temperatureFormatter.report(self.dev, "setpointHeat", hsp)
        EcobeeBase.temperatureFormatter.report(self.dev, "setpointCool", csp)
        self.dev.updateStateOnServer(key="hvacOperationMode", value=HVAC_MODE_MAP[hvacMode])
        self.dev.updateStateOnServer(key="hvacFanMode", value=FAN_MODE_MAP[fanMode])
        self.dev.updateStateOnServer(key="climate", value=climate)
        self.dev.updateStateOnServer(key="modelNumber", value=modelNumber)

        self.dev.updateStateOnServer(key="hvacHeaterIsOn", value=bool(status and ('heatPump' in status or 'auxHeat' in status)))
        self.dev.updateStateOnServer(key="hvacCoolerIsOn", value=bool(status and ('compCool' in status)))
        self.dev.updateStateOnServer(key="hvacFanIsOn", value=bool(status and ('fan' in status or 'ventilator' in status)))

        self.dev.updateStateOnServer(key="autoHome", value=bool(latestEventType and ('autoHome' in latestEventType)))
        self.dev.updateStateOnServer(key="autoAway", value=bool(latestEventType and ('autoAway' in latestEventType)))


## This is the older 'Smart' and 'Smart Si' prior to Ecobee3

class EcobeeSmartThermostat(EcobeeBase):

    def update(self):

        if not self.updatable():
            return

        thermostat = self.ecobee.get_thermostat(self.address)
        if not thermostat:
            return
            
        runtime = thermostat.get('runtime')
        modelNumber = thermostat.get('modelNumber')
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
        self.dev.updateStateOnServer(key="modelNumber", value=modelNumber)

        self.dev.updateStateOnServer(key="hvacHeaterIsOn", value=bool(status and ('heatPump' in status or 'auxHeat' in status)))
        self.dev.updateStateOnServer(key="hvacCoolerIsOn", value=bool(status and ('compCool' in status)))
        self.dev.updateStateOnServer(key="hvacFanIsOn", value=bool(status and ('fan' in status or 'ventilator' in status)))


## All Remote Sensors

class EcobeeRemoteSensor(EcobeeBase):

    def update(self):

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
        occupied = self._update_server_occupancy(matchedSensor)
        self.dev.updateStateOnServer(key="onOffState", value=occupied)
        if occupied:
            self.dev.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
        else:
            self.dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensor)

