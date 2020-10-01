#! /usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import json
import time
import logging

import temperature_scale
import indigo


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
        self.dev = dev
        self.address = dev.address
        self.ecobee = None
        
        self.logger.threaddebug(u"{}: EcobeeThermostat __init__ starting, pluginProps =\n{}".format(dev.name, dev.pluginProps))

        occupancy_id = dev.pluginProps.get('occupancy', None)
        if occupancy_id:
            self.logger.debug(u"{}: adding occupancy device {}".format(dev.name, occupancy_id))
            self.occupancy = indigo.devices[occupancy_id]
        else:
            self.logger.debug(u"{}: no occupancy device".format(dev.name))
            self.occupancy = None
            
        return

                
    def get_climates(self):
        return [
            (key, val)
            for key, val in self.ecobee.thermostats[self.address]["climates"].items()
        ]

    def update(self):

        self.logger.debug(u"{}: Updating device".format(self.dev.name))
        
        # has the Ecobee account been initialized yet?
        if not self.ecobee:

            if len(indigo.activePlugin.ecobee_accounts) == 0:
                self.logger.debug(u"{}: No ecobee accounts available, skipping this device.".format(self.dev.name))
                return
            
            try:
                accountID = int(self.dev.pluginProps["account"])
                self.ecobee = indigo.activePlugin.ecobee_accounts[accountID]
                self.logger.debug(u"{}: Ecobee Account device assigned, {}".format(self.dev.name, accountID))
            except:
                self.logger.error(u"update: Error obtaining ecobee account object")
                return
            
            if not self.ecobee.authenticated:
                self.logger.info('not authenticated to Ecobee servers yet; not initializing state of device {}'.format(self.address))
                return

		try:
        	thermostat_data = self.ecobee.thermostats[self.address]
        except:
                self.logger.debug(u"update: error in thermostat data for address {}".format(self.address))   
                return     
        else:
			if not thermostat_data:
				self.logger.debug("update: no thermostat data found for address {}".format(self.address))
				return
        
        ### fixup code ###
        try:
            for code, dev_id in self.dev.pluginProps["remotes"].items():
                remote = indigo.devices[int(dev_id)]
                if len(remote.address) > 4:
                    newProps = remote.pluginProps
                    newProps["address"] = code
                    remote.replacePluginPropsOnServer(newProps)                
                    self.logger.debug(u"{}: Updated address for remote sensor {}".format(self.dev.name, code))
        except: 
            pass              
        ###################

        
        update_list = []
        
        self.name = thermostat_data.get('name')

        device_type = thermostat_data.get('modelNumber')
        self.dev.updateStateOnServer(key="device_type", value=device_type)
        
        update_list.append({'key' : "device_type", 'value' : device_type})
        
        hsp = thermostat_data.get('desiredHeat')
        update_list.append({'key'           : "setpointHeat", 
                            'value'         : EcobeeThermostat.temperatureFormatter.convert(hsp), 
                            'uiValue'       : EcobeeThermostat.temperatureFormatter.format(hsp),
                            'decimalPlaces' : 1})

        csp = thermostat_data.get('desiredCool')
        update_list.append({'key'           : "setpointCool", 
                            'value'         : EcobeeThermostat.temperatureFormatter.convert(csp), 
                            'uiValue'       : EcobeeThermostat.temperatureFormatter.format(csp),
                            'decimalPlaces' : 1})

        dispTemp = thermostat_data.get('actualTemperature')
        update_list.append({'key'           : "temperatureInput1", 
                            'value'         : EcobeeThermostat.temperatureFormatter.convert(dispTemp), 
                            'uiValue'       : EcobeeThermostat.temperatureFormatter.format(dispTemp),
                            'decimalPlaces' : 1})


        climate = thermostat_data.get('currentClimate')
        update_list.append({'key' : "climate", 'value' : climate})

        hvacMode = thermostat_data.get('hvacMode')
        update_list.append({'key' : "hvacOperationMode", 'value' : HVAC_MODE_MAP[hvacMode]})

        fanMode = thermostat_data.get('desiredFanMode')
        update_list.append({'key' : "hvacFanMode", 'value' : int(FAN_MODE_MAP[fanMode])})

        hum = thermostat_data.get('actualHumidity')
        update_list.append({'key' : "humidityInput1", 'value' : float(hum)})
        
        fanMinOnTime = thermostat_data.get('fanMinOnTime')
        update_list.append({'key' : "fanMinOnTime", 'value' : fanMinOnTime})

        status = thermostat_data.get('equipmentStatus')
        update_list.append({'key' : "equipmentStatus", 'value' : status})

        val = bool(status and ('heatPump' in status or 'auxHeat' in status))
        update_list.append({'key' : "hvacHeaterIsOn", 'value' : val})

        val = bool(status and ('compCool' in status))
        update_list.append({'key' : "hvacCoolerIsOn", 'value' : val})

        val = bool(status and ('fan' in status or 'ventilator' in status))
        update_list.append({'key' : "hvacFanIsOn", 'value' : val})
        
        if device_type in ['athenaSmart', 'nikeSmart', 'apolloSmart', 'vulcanSmart']:
        
            temp2 = thermostat_data.get('internal').get('temperature')
            update_list.append({'key'           : "temperatureInput2", 
                                'value'         : EcobeeThermostat.temperatureFormatter.convert(temp2), 
                                'uiValue'       : EcobeeThermostat.temperatureFormatter.format(temp2),
                                'decimalPlaces' : 1})

            latestEventType = thermostat_data.get('latestEventType')
            update_list.append({'key': "autoHome", 'value' : bool(latestEventType and ('autoHome' in latestEventType))})
            update_list.append({'key': "autoAway", 'value' : bool(latestEventType and ('autoAway' in latestEventType))})

        self.dev.updateStatesOnServer(update_list)


        if self.occupancy:
        
            occupied = thermostat_data.get('internal').get('occupancy')
            self.occupancy.updateStateOnServer(key="onOffState", value = occupied)
            if occupied == u'true' or occupied == u'1':
                self.occupancy.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
            else:
                self.occupancy.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)



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
        self.ecobee.make_request(body, log_msg_action)


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
        self.ecobee.make_request(body, log_msg_action)

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
        self.ecobee.make_request(body, log_msg_action)

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
        self.ecobee.make_request(body, log_msg_action)

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
        self.ecobee.make_request(body, log_msg_action)


class RemoteSensor:

    temperatureFormatter = temperature_scale.Fahrenheit()

    def __init__(self, dev):
        self.logger = logging.getLogger('Plugin.ecobee_devices')
        self.dev = dev
        self.address = dev.address
        self.ecobee = None
        
        self.logger.threaddebug(u"{}: RemoteSensor __init__ starting, pluginProps =\n{}".format(dev.name, dev.pluginProps))

        return


    def update(self):

        self.logger.debug(u"{}: Updating device".format(self.dev.name))
        
        # has the Ecobee account been initialized yet?
        if not self.ecobee:

            if len(indigo.activePlugin.ecobee_accounts) == 0:
                self.logger.debug(u"{}: No ecobee accounts available, skipping this device.".format(self.dev.name))
                return
            
            try:
                accountID = int(self.dev.pluginProps["account"])
                self.ecobee = indigo.activePlugin.ecobee_accounts[accountID]
                self.logger.debug(u"{}: Ecobee Account device assigned, {}".format(self.dev.name, accountID))
            except:
                self.logger.error(u"updatable: Error obtaining ecobee account object")
                return
            
            if not self.ecobee.authenticated:
                self.logger.info('not authenticated to Ecobee servers yet; not initializing state of device {}'.format(self.address))
                return
        
        try: 
            remote_sensor = self.ecobee.sensors[self.address]
        except:
            self.logger.debug("update: no remote sensor data found for address {}".format(self.address))
            return
                
        occupied = remote_sensor.get('occupancy')
        self.dev.updateStateOnServer(key="onOffState", value = occupied)
        if occupied == u'true':
            self.dev.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
        else:
            self.dev.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
       
        temp = remote_sensor.get('temperature')
        
        # check for non-digit values returned when remote is not responding
        if temp.isdigit():
            self.dev.updateStateOnServer( key     = "sensorValue", 
                                        value   = EcobeeThermostat.temperatureFormatter.convert(temp), 
                                        uiValue = EcobeeThermostat.temperatureFormatter.format(temp),
                                        decimalPlaces = 1)


