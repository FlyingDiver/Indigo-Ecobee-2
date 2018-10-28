#! /usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import json
import time
import logging

import temperature_scale
import indigo
import ecobee


ECOBEE_MODELS = {
    'idtSmart'    :    'ecobee Smart',
    'siSmart'     :    'ecobee Si Smart',
    'athenaSmart' :    'ecobee3 Smart',
    'corSmart'    :    'Carrier or Bryant Cor',
    'nikeSmart'   :    'ecobee3 lite Smart',
    'apolloSmart' :    'ecobee4 Smart'
}


#
# All interactions with the Ecobee servers are encapsulated in this class
#

class EcobeeAccount:

    def __init__(self, dev, api_key, refresh_token = None):
        self.logger = logging.getLogger("Plugin.EcobeeAccount")
        self.api_key = api_key
        self.authenticated = False
        self.next_refresh = time.time()
        self.thermostats = {}

        if not dev:
            return
                    
        self.dev = dev
        configDone = dev.pluginProps.get('configDone', False)
        self.logger.debug(u"%s: __init__ configDone = %s" % (dev.name, str(configDone)))
        if not configDone:

            dev.name = "Ecobee Account ({})".format(dev.id)
            dev.replaceOnServer()

            newProps = dev.pluginProps
            newProps["configDone"] = True
            dev.replacePluginPropsOnServer(newProps)

            self.logger.info(u"Configured {}".format(dev.name))

        if refresh_token:
            self.logger.debug("EcobeeAccount __init__, using refresh token = {}".format(refresh_token))
            self.refresh_token = refresh_token
            self.do_token_refresh()
            
        if self.authenticated:
            self.server_update()
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
        else:
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
        
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
                            '"includeEvents":"true",'
                            '"includeProgram":"true",'
                            '"includeEquipmentStatus":"true",'
                            '"includeSettings":"true"}}')}
        try:
            request = requests.get('https://api.ecobee.com/1/thermostat', headers=header, params=params)
        except requests.RequestException, e:
            self.logger.error("Thermostat Update Error, exception = {}".format(e))
            return
            
        if request.status_code != requests.codes.ok:
            self.logger.error("Thermostat Update failed, response = '{}'".format(request.text))                

        serverData = request.json()['thermostatList']
        self.logger.debug("Thermostat Update OK, got info on {} devices".format(len(serverData)))
        self.logger.threaddebug("{}".format(serverData))
            
        # Extract the relevant info from the server data and put it in a convenient Dict form
        
        for therm in serverData:
        
            identifier = therm["identifier"]
            
            self.thermostats[identifier] = {    "name"              : therm["name"], 
                                                "brand"             : therm["brand"], 
                                                "modelNumber"       : therm["modelNumber"],
                                                "equipmentStatus"   : therm["equipmentStatus"],
                                                "currentClimate"    : therm["program"]["currentClimateRef"],
                                                "hvacMode"          : therm["settings"]["hvacMode"],
                                                "fanMinOnTime"      : therm["settings"]["fanMinOnTime"],
                                                "desiredCool"       : therm["runtime"]["desiredCool"],
                                                "desiredHeat"       : therm["runtime"]["desiredHeat"],
                                                "actualTemperature" : therm["runtime"]["actualTemperature"],
                                                "actualHumidity"    : therm["runtime"]["actualHumidity"],
                                                "desiredFanMode"    : therm["runtime"]["desiredFanMode"] }

            if therm.get('events') and len(therm.get('events')) > 0:
                latestEventType = therm.get('events')[0].get('type')
            else:
                latestEventType = None
            self.thermostats[identifier]["latestEventType"] = latestEventType

            remotes = {}
            for remote in therm[u"remoteSensors"]:
                if remote["type"] == "ecobee3_remote_sensor":
                    code = remote[u"code"]
                    remotes[code] = {u"name": remote[u"name"]}
                    for cap in remote["capability"]:
                        remotes[code][cap["type"]] = cap["value"]
                elif remote["type"] == "thermostat":
                    internal = {}
                    for cap in remote["capability"]:
                        internal[cap["type"]] = cap["value"]
                    self.thermostats[identifier]["internal"] = internal
            self.thermostats[identifier]["remotes"] = remotes
            
        self.logger.debug("Thermostat Update, thermostats =\n{}".format(self.thermostats))
                
            
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
    'auxHeatOnly' : indigo.kHvacMode.Heat,
    'off'         : indigo.kHvacMode.Off
    }

FAN_MODE_MAP = {
    'auto': indigo.kFanMode.Auto,
    'on'  : indigo.kFanMode.AlwaysOn
    }



class EcobeeThermostat:

    temperatureFormatter = temperature_scale.Fahrenheit()

    def __init__(self, dev):
        self.logger = logging.getLogger('Plugin.ecobee_devices')
        self.logger.debug(u"{}: EcobeeThermostat __init__".format(dev.name))
        self.dev = dev
        self.address = dev.address
        
        try:
            accountID = int(self.dev.pluginProps["account"])
            self.ecobee = indigo.activePlugin.ecobee_accounts[accountID]
        except:
            self.ecobee = None
            return

        if dev.pluginProps.get('configDone', False):
        
            occupancy_id = dev.pluginProps.get('occupancy', None)
            self.logger.debug(u"{}: adding occupancy device {}".format(dev.name, occupancy_id))
            if occupancy_id:
                self.occupancy = indigo.devices[occupancy_id]
                
            remote_list = dev.pluginProps.get('remotes', None)
            self.logger.debug(u"{}: adding remote list {}".format(dev.name, remote_list))
            if len(remote_list) > 0:
                self.remotes = {}
                for code, rem_id in remote_list.items():
                    self.remotes[code] = indigo.devices[int(rem_id)]
                
            return

#
#       This code only executed once for each device after it's created   
#
        self.logger.debug(u"{}: doing initial config in __init__".format(dev.name))

        thermostat = self.ecobee.thermostats.get(self.address)
        if not thermostat:
            self.logger.debug("Ecobee __init__: no thermostat found for address {}".format(self.address))
            return

        device_type = thermostat.get('modelNumber')
        name = thermostat.get('name')

        dev.name = "Ecobee {} ({})".format(name, self.address)
        dev.subModel = ECOBEE_MODELS[device_type]
        dev.replaceOnServer()

        if  device_type in  ['athenaSmart', 'corSmart']:

            # set props for this specific device type
            
            newProps = dev.pluginProps
            newProps["device_type"] = device_type
            newProps["configDone"] = True
            newProps["NumHumidityInputs"] = 1
            newProps["NumTemperatureInputs"] = 2
            newProps["ShowCoolHeatEquipmentStateUI"] = True
            
            # Create the integral occupancy sensor.
            
            self.logger.info(u"Adding Occupancy Sensor to '{}' ({})".format(dev.name, dev.id))
            newdev = indigo.device.create(indigo.kProtocol.Plugin, 
                                            address=dev.address,
                                            name=dev.name + " Occupancy",
                                            deviceTypeId="OccupancySensor", 
                                            groupWithDevice=dev.id,
                                            props={ 'configDone': True, 
                                                    'SupportsStatusRequest': False,
                                                    'account': self.dev.pluginProps["account"],
                                                    'address': self.dev.pluginProps["address"]
                                                },
                                            folder=dev.folderId)   
            newdev.model = dev.model
            newdev.subModel = "Occupancy"
            newdev.replaceOnServer()    

            self.occupancy = newdev
            newProps["occupancy"] = newdev.id
            
            # Create any linked remote sensors
            
            remotes = thermostat.get("remotes")
            self.logger.debug(u"{}: {} remotes".format(dev.name, len(remotes)))
                        
            remote_ids = indigo.Dict()
            self.remotes = []
            
            for code, rem in remotes.items():
                
                self.logger.info(u"Adding Remote Sensor {} to '{}' ({})".format(code, dev.name, dev.id))

                remote_name = "{} Remote - {} ({})".format(dev.name, rem["name"], code)
                newdev = indigo.device.create(indigo.kProtocol.Plugin, 
                                                address=dev.address,
                                                name=remote_name,
                                                deviceTypeId="RemoteSensor", 
                                                groupWithDevice=dev.id,
                                                props={ 'configDone': True, 
                                                        'SupportsSensorValue': True,
                                                        'SupportsStatusRequest': False,
                                                        'account': self.dev.pluginProps["account"],
                                                        'address': self.dev.pluginProps["address"]
                                                    },
                                                folder=dev.folderId)
                                            
                newdev.model = dev.model
                newdev.subModel = "Remote"
                newdev.replaceOnServer()    
                newdev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensor)
                remote_ids[code] = str(newdev.id)
                self.remotes.append(newdev)
                    
            newProps["remotes"] = remote_ids
            dev.replacePluginPropsOnServer(newProps)
           

        elif device_type == 'idtSmart':

            newProps = dev.pluginProps
            newProps["device_type"] = device_type
            newProps["configDone"] = True
            newProps["NumHumidityInputs"] = 1
            newProps["ShowCoolHeatEquipmentStateUI"] = True
            dev.replacePluginPropsOnServer(newProps)

            
            
        self.logger.info(u"Configured {}".format(dev.name))

                
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

    def get_climates(self):
        thermostat = self.ecobee.get_thermostat(self.address)
        return [
            (rs.get('climateRef'), rs.get('name'))
            for rs in thermostat.get('program').get('climates')
        ]

    def update(self):

        if not self.updatable():
            return

        thermostat = self.ecobee.thermostats[self.address]
        if not thermostat:
            self.logger.debug("update: no thermostat found for address {}".format(self.address))
            return
        
        update_list = []
        
        self.name = thermostat.get('name')

        device_type = thermostat.get('modelNumber')
        self.dev.updateStateOnServer(key="device_type", value=device_type)
        
        update_list.append({'key' : "device_type", 'value' : device_type})
        
        hsp = thermostat.get('desiredHeat')
        update_list.append({'key'           : "setpointHeat", 
                            'value'         : EcobeeThermostat.temperatureFormatter.convert(hsp), 
                            'uiValue'       : EcobeeThermostat.temperatureFormatter.format(hsp),
                            'decimalPlaces' : 1})

        csp = thermostat.get('desiredCool')
        update_list.append({'key'           : "setpointCool", 
                            'value'         : EcobeeThermostat.temperatureFormatter.convert(csp), 
                            'uiValue'       : EcobeeThermostat.temperatureFormatter.format(csp),
                            'decimalPlaces' : 1})

        dispTemp = thermostat.get('actualTemperature')
        update_list.append({'key'           : "temperatureInput1", 
                            'value'         : EcobeeThermostat.temperatureFormatter.convert(dispTemp), 
                            'uiValue'       : EcobeeThermostat.temperatureFormatter.format(dispTemp),
                            'decimalPlaces' : 1})


        climate = thermostat.get('currentClimate')
        update_list.append({'key' : "climate", 'value' : climate})

        hvacMode = thermostat.get('hvacMode')
        update_list.append({'key' : "hvacOperationMode", 'value' : HVAC_MODE_MAP[hvacMode]})

        fanMode = thermostat.get('desiredFanMode')
        update_list.append({'key' : "hvacFanMode", 'value' : int(FAN_MODE_MAP[fanMode])})

        hum = thermostat.get('actualHumidity')
        update_list.append({'key' : "humidityInput1", 'value' : float(hum)})
        
        fanMinOnTime = thermostat.get('fanMinOnTime')
        update_list.append({'key' : "fanMinOnTime", 'value' : fanMinOnTime})

        status = thermostat.get('equipmentStatus')
        update_list.append({'key' : "equipmentStatus", 'value' : status})

        val = bool(status and ('heatPump' in status or 'auxHeat' in status))
        update_list.append({'key' : "hvacHeaterIsOn", 'value' : val})

        val = bool(status and ('compCool' in status))
        update_list.append({'key' : "hvacCoolerIsOn", 'value' : val})

        val = bool(status and ('fan' in status or 'ventilator' in status))
        update_list.append({'key' : "hvacFanIsOn", 'value' : val})
        
        if device_type in ['athenaSmart', 'corSmart', 'nikeSmart', 'apolloSmart']:
        
            temp2 = thermostat.get('internal').get('temperature')
            update_list.append({'key'           : "temperatureInput2", 
                                'value'         : EcobeeThermostat.temperatureFormatter.convert(temp2), 
                                'uiValue'       : EcobeeThermostat.temperatureFormatter.format(temp2),
                                'decimalPlaces' : 1})

            latestEventType = thermostat.get('latestEventType')
            update_list.append({'key': "autoHome", 'value' : bool(latestEventType and ('autoHome' in latestEventType))})
            update_list.append({'key': "autoAway", 'value' : bool(latestEventType and ('autoAway' in latestEventType))})

        self.dev.updateStatesOnServer(update_list)


        if self.occupancy:
        
            occupied = thermostat.get('internal').get('occupancy')
            self.occupancy.updateStateOnServer(key="onOffState", value = occupied)
            if occupied == u'true':
                self.occupancy.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
            else:
                self.occupancy.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)


        # if there are linked Remote Sensor devices, update them

        if self.remotes:
        
            for code, remote in self.remotes.items():
            
                occupied = thermostat.get('remotes').get(code).get('occupancy')
                remote.updateStateOnServer(key="onOffState", value = occupied)
                if occupied == u'true':
                    remote.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
                else:
                    remote.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
               
                temp = thermostat.get('remotes').get(code).get('temperature')
                remote.updateStateOnServer( key     = "sensorValue", 
                                            value   = EcobeeThermostat.temperatureFormatter.convert(temp), 
                                            uiValue = EcobeeThermostat.temperatureFormatter.format(temp),
                                            decimalPlaces = 1)



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



 