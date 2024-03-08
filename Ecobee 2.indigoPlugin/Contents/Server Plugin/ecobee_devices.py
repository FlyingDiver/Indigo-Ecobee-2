#! /usr/bin/env python
# -*- coding: utf-8 -*-

import temperature_scale
import indigo
import logging

HVAC_MODE_MAP = {
    'heat': indigo.kHvacMode.Heat,
    'cool': indigo.kHvacMode.Cool,
    'auto': indigo.kHvacMode.HeatCool,
    'auxHeatOnly': indigo.kHvacMode.Heat,
    'off': indigo.kHvacMode.Off
}

FAN_MODE_MAP = {
    'auto': indigo.kFanMode.Auto,
    'on': indigo.kFanMode.AlwaysOn
}


class EcobeeDevice(object):
    temperatureFormatter = temperature_scale.Fahrenheit()

    def __init__(self, dev):
        self.logger = logging.getLogger('Plugin.ecobee_devices')
        self.devID = dev.id
        self.address = dev.address
        self.name = dev.name
        self.ecobee = None

        self.logger.threaddebug(f"{dev.name}: EcobeeDevice __init__ starting, pluginProps =\n{dev.pluginProps}")


class EcobeeThermostat(EcobeeDevice):

    def __init__(self, dev):
        super(EcobeeThermostat, self).__init__(dev)

        self.lastHeatSetpoint = dev.heatSetpoint
        self.logger.debug(f"{dev.name}: __init__  lastHeatSetpoint: {self.lastHeatSetpoint}")
        self.lastCoolSetpoint = dev.coolSetpoint
        self.logger.debug(f"{dev.name}: __init__  lastCoolSetpoint: {self.lastCoolSetpoint}")

        occupancy_id = dev.pluginProps.get('occupancy', None)
        if occupancy_id:
            self.logger.debug(f"{dev.name}: adding occupancy device {occupancy_id}")
            self.occupancy = indigo.devices[occupancy_id]
        else:
            self.logger.debug(f"{dev.name}: no occupancy device")
            self.occupancy = None

    def get_climates(self):
        return [
            (key, val)
            for key, val in self.ecobee.thermostats[self.address]["climates"].items()
        ]

    def update(self):

        self.logger.debug(f"{self.name}: Updating device")
        device = indigo.devices[self.devID]

        # has the Ecobee account been initialized yet?
        if not self.ecobee:

            if len(indigo.activePlugin.ecobee_accounts) == 0:
                self.logger.debug(f"{self.name}: No ecobee accounts available, skipping this device.")
                return

            try:
                accountID = device.pluginProps["account"]
                self.ecobee = indigo.activePlugin.ecobee_accounts[accountID]
                self.logger.debug(f"{self.name}: Ecobee Account device assigned, {accountID}")
            except (Exception,):
                self.logger.error("update: Error obtaining ecobee account object")
                return

            if not self.ecobee.authenticated:
                self.logger.info(f"not authenticated to Ecobee servers yet; not initializing state of device {self.address}")
                return

        try:
            thermostat_data = self.ecobee.thermostats[self.address]
        except (Exception,):
            self.logger.debug(f"update: error in thermostat data for address {self.address}")
            return
        else:
            if not thermostat_data:
                self.logger.debug(f"update: no thermostat data found for address {self.address}")
                return

        # fixup code
        try:
            for code, dev_id in device.pluginProps["remotes"].items():
                remote = indigo.devices[int(dev_id)]
                if len(remote.address) > 4:
                    newProps = remote.pluginProps
                    newProps["address"] = code
                    remote.replacePluginPropsOnServer(newProps)
                    self.logger.debug(f"{self.name}: Updated address for remote sensor {code}")
        except (Exception,):
            pass
            ###################

        self.logger.debug(f"{self.name}: {thermostat_data=}")

        update_list = [{'key': "latestEventType", 'value': thermostat_data.get('latestEventType')}]

        hsp = thermostat_data.get('desiredHeat')
        self.lastHeatSetpoint = EcobeeDevice.temperatureFormatter.convertFromEcobee(hsp)
        self.logger.debug(f"{device.name}: Reported hsp: {hsp}, converted hsp: {self.lastHeatSetpoint}")
        update_list.append({'key': "setpointHeat",
                            'value': EcobeeDevice.temperatureFormatter.convertFromEcobee(hsp),
                            'uiValue': EcobeeDevice.temperatureFormatter.format(hsp),
                            'decimalPlaces': 1})

        csp = thermostat_data.get('desiredCool')
        self.lastCoolSetpoint = EcobeeDevice.temperatureFormatter.convertFromEcobee(csp)
        self.logger.debug(f"{device.name}: Reported csp: {csp}, converted csp: {self.lastCoolSetpoint}")
        update_list.append({'key': "setpointCool",
                            'value': EcobeeDevice.temperatureFormatter.convertFromEcobee(csp),
                            'uiValue': EcobeeDevice.temperatureFormatter.format(csp),
                            'decimalPlaces': 1})

        dispTemp = thermostat_data.get('actualTemperature')
        self.logger.debug(f"{device.name}: Reported dispTemp: {dispTemp}, converted dispTemp: {EcobeeDevice.temperatureFormatter.convertFromEcobee(dispTemp)}")
        update_list.append({'key': "temperatureInput1",
                            'value': EcobeeDevice.temperatureFormatter.convertFromEcobee(dispTemp),
                            'uiValue': EcobeeDevice.temperatureFormatter.format(dispTemp),
                            'decimalPlaces': 1})

        climate = thermostat_data.get('currentClimate')
        update_list.append({'key': "climate", 'value': climate})

        hvacMode = thermostat_data.get('hvacMode')
        update_list.append({'key': "hvacMode", 'value': hvacMode})
        update_list.append({'key': "hvacOperationMode", 'value': HVAC_MODE_MAP[hvacMode]})

        fanMode = thermostat_data.get('desiredFanMode')
        update_list.append({'key': "hvacFanMode", 'value': int(FAN_MODE_MAP[fanMode])})

        hum = thermostat_data.get('actualHumidity')
        update_list.append({'key': "humidityInput1", 'value': float(hum)})

        fanMinOnTime = thermostat_data.get('fanMinOnTime')
        update_list.append({'key': "fanMinOnTime", 'value': fanMinOnTime})

        status = thermostat_data.get('equipmentStatus')
        update_list.append({'key': "equipmentStatus", 'value': status})

        val = bool(status and ('heatPump' in status or 'auxHeat' in status))
        update_list.append({'key': "hvacHeaterIsOn", 'value': val})

        val = bool(status and ('compCool' in status))
        update_list.append({'key': "hvacCoolerIsOn", 'value': val})

        val = bool(status and ('fan' in status or 'ventilator' in status))
        update_list.append({'key': "hvacFanIsOn", 'value': val})

        device_type = thermostat_data.get('modelNumber')
        update_list.append({'key': "device_type", 'value': device_type})

        if device_type in ['athenaSmart', 'nikeSmart', 'apolloSmart', 'vulcanSmart', 'aresSmart']:

            internalTemp = thermostat_data.get('internal').get('temperature')
            try:
                convertedTemp = EcobeeDevice.temperatureFormatter.convertFromEcobee(internalTemp)
            except (Exception,):
                self.logger.warning(f"{device.name}: Error converting internalTemp {internalTemp}")
            else:
                self.logger.debug(f"{device.name}: Reported internalTemp: {internalTemp}, converted internalTemp: {convertedTemp}")
                update_list.append({'key': "temperatureInput2",
                                    'value': convertedTemp,
                                    'uiValue': EcobeeDevice.temperatureFormatter.format(internalTemp),
                                    'decimalPlaces': 1})

            latestEventType = thermostat_data.get('latestEventType')
            update_list.append({'key': "autoHome", 'value': bool(latestEventType and ('autoHome' in latestEventType))})
            update_list.append({'key': "autoAway", 'value': bool(latestEventType and ('autoAway' in latestEventType))})

        device.updateStatesOnServer(update_list)

        if self.occupancy:

            occupied = thermostat_data.get('internal').get('occupancy')
            self.occupancy.updateStateOnServer(key="onOffState", value=occupied)
            if occupied == 'true' or occupied == '1':
                self.occupancy.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
            else:
                self.occupancy.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)

    def set_hvac_mode(self, hvac_mode):  # possible hvac modes are auto, auxHeatOnly, cool, heat, off
        body = {
            "selection":
                {"selectionType": "thermostats", "selectionMatch": self.address},
            "thermostat":
                {"settings": {"hvacMode": hvac_mode}}
        }
        log_msg_action = "set HVAC mode"
        self.ecobee.make_request(body, log_msg_action)

    def set_hold_cool(self, cool_temp, hold_type="nextTransition"):
        self.logger.debug(f"{self.name}: set_hold_cool: {cool_temp}")
        self.lastCoolSetpoint = cool_temp
        self.set_hold_temp(cool_temp, self.lastHeatSetpoint, hold_type)

    def set_hold_heat(self, heat_temp, hold_type="nextTransition"):
        self.logger.debug(f"{self.name}: set_hold_heat: {heat_temp}")
        self.lastHeatSetpoint = heat_temp
        self.set_hold_temp(self.lastCoolSetpoint, heat_temp, hold_type)

    def set_hold_temp(self, cool_temp, heat_temp, hold_type="nextTransition"):  # Set a hold
        self.logger.debug(f"{self.name}: set_hold_temp, cool_temp: {cool_temp}, heat_temp: {heat_temp}")

        eb_cool_temp = EcobeeDevice.temperatureFormatter.convertToEcobee(cool_temp)
        eb_heat_temp = EcobeeDevice.temperatureFormatter.convertToEcobee(heat_temp)
        self.logger.debug(f"{self.name}: Converted setpoints cool: {eb_cool_temp}, heat: {eb_heat_temp}")

        body = {
            "selection":
                {"selectionType": "thermostats", "selectionMatch": self.address},
            "functions":
                [{"type": "setHold", "params": {"holdType": hold_type, "coolHoldTemp": eb_cool_temp, "heatHoldTemp": eb_heat_temp}}]
        }
        log_msg_action = "set hold temp"
        self.ecobee.make_request(body, log_msg_action)

    def set_hold_temp_with_fan(self, cool_temp, heat_temp, hold_type="nextTransition"):  # Set a fan hold
        self.logger.debug(f"{self.name}: set_hold_temp_with_fan, cool_temp: {cool_temp}, heat_temp: {heat_temp}")

        eb_cool_temp = EcobeeDevice.temperatureFormatter.convertToEcobee(cool_temp)
        eb_heat_temp = EcobeeDevice.temperatureFormatter.convertToEcobee(heat_temp)
        self.logger.debug(f"{self.name}: Converted setpoints cool: {eb_cool_temp}, heat: {eb_heat_temp}")

        body = {
            "selection":
                {"selectionType": "thermostats", "selectionMatch": self.address},
            "functions":
                [{
                    "type": "setHold",
                    "params": {"holdType": hold_type, "coolHoldTemp": eb_cool_temp, "heatHoldTemp": eb_heat_temp, "fan": "on"}
                }]
        }
        log_msg_action = "set hold temp with fan on"
        self.ecobee.make_request(body, log_msg_action)

    def set_climate_hold(self, climate, hold_type="nextTransition"):  # Set a climate hold - ie away, home, sleep
        body = {
            "selection":
                {"selectionType": "thermostats", "selectionMatch": self.address},
            "functions":
                [{
                    "type": "setHold",
                    "params": {"holdType": hold_type, "holdClimateRef": climate}
                 }]
        }
        log_msg_action = "set climate hold"
        self.ecobee.make_request(body, log_msg_action)

    def resume_program(self):  # Resume currently scheduled program
        body = {
            "selection":
                {"selectionType": "thermostats", "selectionMatch": self.address},
            "functions":
                [{
                    "type": "resumeProgram", "params": {"resumeAll": "False"}
                }]
        }
        log_msg_action = "resume program"
        self.ecobee.make_request(body, log_msg_action)

class RemoteSensor(EcobeeDevice):

    def update(self):

        self.logger.debug(f"{self.name}: Updating device")
        device = indigo.devices[self.devID]

        # has the Ecobee account been initialized yet?
        if not self.ecobee:

            if len(indigo.activePlugin.ecobee_accounts) == 0:
                self.logger.debug(f"{self.name}: No ecobee accounts available, skipping this device.")
                return

            try:
                accountID = device.pluginProps["account"]
                self.ecobee = indigo.activePlugin.ecobee_accounts[accountID]
                self.logger.debug(f"{self.name}: Ecobee Account device assigned, {accountID}")
            except (Exception,):
                self.logger.error("update: Error obtaining ecobee account object")
                return

            if not self.ecobee.authenticated:
                self.logger.info(f'not authenticated to Ecobee servers yet; not initializing state of device {self.address}')
                return

        try:
            remote_sensor = self.ecobee.sensors[self.address]
        except (Exception,):
            self.logger.debug(f"update: no remote sensor data found for address {self.address}")
            return

        occupied = remote_sensor.get('occupancy')
        device.updateStateOnServer(key="onOffState", value=occupied)
        if occupied == 'true':
            device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
        else:
            device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)

        temp = remote_sensor.get('temperature')

        # check for non-digit values returned when remote is not responding
        if temp.isdigit():
            self.logger.debug(f"{device.name}: Reported temp: {temp}, converted temp: {EcobeeDevice.temperatureFormatter.convertFromEcobee(temp)}")
            device.updateStateOnServer(key="sensorValue",
                                       value=EcobeeDevice.temperatureFormatter.convertFromEcobee(temp),
                                       uiValue=EcobeeDevice.temperatureFormatter.format(temp),
                                       decimalPlaces=1)
