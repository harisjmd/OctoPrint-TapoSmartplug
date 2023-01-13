# coding=utf-8
from __future__ import absolute_import

import logging
import os
import re
import sqlite3
import threading
import time
from builtins import bytes

import flask
import octoprint.plugin
from flask_babel import gettext
from octoprint.access.permissions import Permissions, ADMIN_GROUP
from octoprint.events import Events
from octoprint.util import RepeatedTimer
from uptime import uptime

from .api import TapoSmartPlugApi, Commands
from .settings import PlugSettings, TapoSettings
from .tapo import TapoPlugAdapter
from .utils import encode_string, decode_string

ON_STATE = "on"
OFF_STATE = "off"
CURRENT_STATE = "currentState"

try:
	from octoprint.util import ResettableTimer
except:
	from .settings import ResettableTimer


class taposmartplugPlugin(
		octoprint.plugin.SettingsPlugin,
		octoprint.plugin.AssetPlugin,
		octoprint.plugin.TemplatePlugin,
		octoprint.plugin.SimpleApiPlugin,
		octoprint.plugin.StartupPlugin,
		octoprint.plugin.ProgressPlugin,
		octoprint.plugin.EventHandlerPlugin
):

	def __init__(self):
		super().__init__()
		self._tapo_settings = TapoSettings()
		self._api = TapoSmartPlugApi(self)

		self.idleTimeoutWaitTemp = self._tapo_settings.get_default_settings()[TapoSettings.IDLE_IGNORE_COMMANDS]
		self._idleIgnoreCommandsArray = self._tapo_settings.get_default_settings()[TapoSettings.IDLE_IGNORE_COMMANDS]
		self.idleIgnoreCommands = self._tapo_settings.get_default_settings()[TapoSettings.IDLE_IGNORE_COMMANDS]
		self.idleTimeout = self._tapo_settings.get_default_settings()[TapoSettings.IDLE_TIMEOUT]
		self.poll_status = None
		self._logger = logging.getLogger("octoprint.plugins.taposmartplug")
		self._taposmartplug_logger = logging.getLogger("octoprint.plugins.taposmartplug.debug")
		self.abortTimeout = self._tapo_settings.get_default_settings()[TapoSettings.ABORT_TIMEOUT]
		self._timeout_value = None
		self._abort_timer = None
		self._countdown_active = False
		self.print_job_started = False
		self._waitForHeaters = False
		self._waitForTimelapse = False
		self._timelapse_active = False
		self._skipIdleTimer = False
		self.powerOffWhenIdle = self._tapo_settings.get_default_settings()[TapoSettings.POWER_OFF_WHEN_IDLE]
		self._idleTimer = None
		self._autostart_file = None
		self.db_path = None

	# StartupPlugin mixin
	def on_startup(self, host, port):
		# setup customized logger
		from octoprint.logging.handlers import CleaningTimedRotatingFileHandler
		taposmartplug_logging_handler = CleaningTimedRotatingFileHandler(
				self._settings.get_plugin_logfile_path(postfix="debug"), when="D", backupCount=3
		)
		taposmartplug_logging_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
		taposmartplug_logging_handler.setLevel(logging.DEBUG)

		self._taposmartplug_logger.addHandler(taposmartplug_logging_handler)
		self._taposmartplug_logger.setLevel(
				logging.DEBUG if self._settings.get_boolean([TapoSettings.DEBUG_LOGGING]) else logging.INFO
		)
		self._taposmartplug_logger.propagate = False

		self.db_path = os.path.join(self.get_plugin_data_folder(), "energy_data.db")
		if not os.path.exists(self.db_path):
			db = sqlite3.connect(self.db_path)
			cursor = db.cursor()
			cursor.execute(
					'''CREATE TABLE energy_data(id INTEGER PRIMARY KEY, ip TEXT, timestamp TEXT, current REAL,
					power REAL, total REAL, voltage REAL) '''
			)
			db.commit()
			db.close()

	def on_after_startup(self):
		self._logger.info("TapoSmartplug loaded!")
		if self._settings.get([TapoSettings.POLLING_ENABLED]):
			self.poll_status = RepeatedTimer(
					int(self._settings.get([TapoSettings.POLLING_INTERVAL])) * 60,
					self.check_statuses
			)
			self.poll_status.start()

		self.abortTimeout = self._settings.get_int([TapoSettings.ABORT_TIMEOUT])
		self._taposmartplug_logger.debug("abortTimeout: %s" % self.abortTimeout)

		self.powerOffWhenIdle = self._settings.get_boolean([TapoSettings.POWER_OFF_WHEN_IDLE])
		self._taposmartplug_logger.debug("powerOffWhenIdle: %s" % self.powerOffWhenIdle)

		self.idleTimeout = self._settings.get_int([TapoSettings.IDLE_TIMEOUT])
		self._taposmartplug_logger.debug("idleTimeout: %s" % self.idleTimeout)
		self.idleIgnoreCommands = self._settings.get([TapoSettings.IDLE_IGNORE_COMMANDS])
		self._idleIgnoreCommandsArray = self.idleIgnoreCommands.split(',') if self.idleIgnoreCommands is not None else ""
		self._taposmartplug_logger.debug("idleIgnoreCommands: %s" % self.idleIgnoreCommands)
		self.idleTimeoutWaitTemp = self._settings.get_int([TapoSettings.IDLE_TIMEOUT_WAIT_TEMP])
		self._taposmartplug_logger.debug("idleTimeoutWaitTemp: %s" % self.idleTimeoutWaitTemp)
		if self._settings.get_boolean([TapoSettings.EVENT_ON_STARTUP_MONITORING]) is True:
			self._taposmartplug_logger.debug("powering on due to startup.")
			for plug in self._settings.get([TapoSettings.SMART_PLUGS_LIST]):
				if plug[PlugSettings.event_on_startup] is True:
					self._taposmartplug_logger.debug("powering on %s due to startup." % (plug[PlugSettings.ip]))
					response = self.turn_on(plug[PlugSettings.ip])
					if response.get(CURRENT_STATE, False) == ON_STATE:
						self._plugin_manager.send_plugin_message(self._identifier, response)
					else:
						self._taposmartplug_logger.debug(
								"powering on %s during startup failed." % (plug[PlugSettings.ip])
						)
		self._reset_idle_timer()

	# SettingsPlugin mixin

	def get_settings_defaults(self):
		return TapoSettings.get_default_settings()

	def on_settings_load(self):
		current_settings = octoprint.plugin.SettingsPlugin.on_settings_load(self)
		if TapoSettings.SMART_PLUGS_LIST in current_settings:
			smart_plugs_load = current_settings.get(TapoSettings.SMART_PLUGS_LIST)
			for p in smart_plugs_load:
				p[PlugSettings.password] = decode_string(p[PlugSettings.password])
		return current_settings

	def on_settings_save(self, data):
		old_debug_logging = self._settings.get_boolean([TapoSettings.DEBUG_LOGGING])
		old_polling_value = self._settings.get_boolean([TapoSettings.POLLING_ENABLED])
		old_polling_timer = self._settings.get([TapoSettings.POLLING_INTERVAL])
		old_powerOffWhenIdle = self._settings.get_boolean([TapoSettings.POWER_OFF_WHEN_IDLE])
		old_idleTimeout = self._settings.get_int([TapoSettings.IDLE_TIMEOUT])
		old_idleIgnoreCommands = self._settings.get([TapoSettings.IDLE_IGNORE_COMMANDS])
		old_idleTimeoutWaitTemp = self._settings.get_int([TapoSettings.IDLE_TIMEOUT_WAIT_TEMP])
		old_polling_timer = self._settings.get([TapoSettings.POLLING_INTERVAL])

		if TapoSettings.SMART_PLUGS_LIST in data:
			smart_plugs_save = data.get(TapoSettings.SMART_PLUGS_LIST)
			for p in smart_plugs_save:
				p[PlugSettings.password] = encode_string(p[PlugSettings.password])

		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

		self.abortTimeout = self._settings.get_int([TapoSettings.ABORT_TIMEOUT])
		self.powerOffWhenIdle = self._settings.get_boolean([TapoSettings.POWER_OFF_WHEN_IDLE])

		self.idleTimeout = self._settings.get_int([TapoSettings.IDLE_TIMEOUT])
		self.idleIgnoreCommands = self._settings.get([TapoSettings.IDLE_IGNORE_COMMANDS])
		self._idleIgnoreCommandsArray = self.idleIgnoreCommands.split(',') if self.idleIgnoreCommands is not None else ""
		self.idleTimeoutWaitTemp = self._settings.get_int([TapoSettings.IDLE_TIMEOUT_WAIT_TEMP])

		if self.powerOffWhenIdle != old_powerOffWhenIdle:
			self._plugin_manager.send_plugin_message(
					self._identifier,
					dict(
							powerOffWhenIdle=self.powerOffWhenIdle, type="timeout",
							timeout_value=self._timeout_value
					)
			)

		if self.powerOffWhenIdle:
			self._taposmartplug_logger.debug("Settings saved, Automatic Power Off Enabled, starting idle timer...")
			self._reset_idle_timer()

		new_debug_logging = self._settings.get_boolean([TapoSettings.DEBUG_LOGGING])
		new_polling_value = self._settings.get_boolean([TapoSettings.POLLING_ENABLED])
		new_polling_timer = self._settings.get([TapoSettings.POLLING_INTERVAL])

		if old_debug_logging != new_debug_logging:
			if new_debug_logging:
				self._taposmartplug_logger.setLevel(logging.DEBUG)
			else:
				self._taposmartplug_logger.setLevel(logging.INFO)

		if old_polling_value != new_polling_value or old_polling_timer != new_polling_timer:
			if self.poll_status:
				self.poll_status.cancel()

			if new_polling_value:
				self.poll_status = RepeatedTimer(
						int(self._settings.get([TapoSettings.POLLING_INTERVAL])) * 60,
						self.check_statuses
				)
				self.poll_status.start()

		self.check_statuses()

	def get_settings_version(self):
		return TapoSettings.get_version()

	def on_settings_migrate(self, target, current=None):
		self._tapo_settings.on_settings_migrate(self._logger, self._settings, target, current)

	# AssetPlugin mixin

	def get_assets(self):
		return dict(
				js=[
					"js/jquery-ui.min.js",
					"js/knockout-sortable.1.2.0.js",
					"js/fontawesome-iconpicker.js",
					"js/ko.iconpicker.js",
					"js/taposmartplug.js",
					"js/knockout-bootstrap.min.js",
					"js/ko.observableDictionary.js",
					"js/plotly-latest.min.js"
				],
				css=[
					"css/font-awesome.min.css",
					"css/font-awesome-v4-shims.min.css",
					"css/fontawesome-iconpicker.css",
					"css/taposmartplug.css"
				]
		)

	# TemplatePlugin mixin

	def get_template_configs(self):
		templates_to_load = [
			dict(type="navbar", custom_bindings=True),
			dict(type="settings", custom_bindings=True),
			dict(
					type="sidebar", icon="plug", custom_bindings=True, data_bind="visible: smart_plugs_list().length > 0",
					template="taposmartplug_sidebar.jinja2", template_header="taposmartplug_sidebar_header.jinja2"
			),
			dict(
					type="tab", custom_bindings=True, data_bind="visible: show_sidebar()",
					template="taposmartplug_tab.jinja2"
			)]
		return templates_to_load

	# ProgressPlugin mixin

	def on_print_progress(self, storage, path, progress):
		if self._settings.get_boolean([TapoSettings.PROGRESS_POLLING]) is False:
			return
		self._taposmartplug_logger.debug("Checking statuses during print progress (%s)." % progress)
		_print_progress_timer = threading.Timer(1, self.check_statuses)
		_print_progress_timer.daemon = True
		_print_progress_timer.start()
		self._plugin_manager.send_plugin_message(self._identifier, dict(updatePlot=True))

		if self.powerOffWhenIdle and not self._skipIdleTimer:
			self._taposmartplug_logger.debug("Resetting idle timer during print progress (%s)..." % progress)
			self._waitForHeaters = False
			self._reset_idle_timer()

	#  SimpleApiPlugin mixin

	def turn_on(self, plug_ip):
		plug = self.plug_search(self._settings.get([TapoSettings.SMART_PLUGS_LIST]), PlugSettings.ip, plug_ip)
		tapo_plug_adapter = TapoPlugAdapter.create_tapo_plug_adapter(plug)
		status = tapo_plug_adapter.send_turn_on()

		if plug[PlugSettings.autoConnect] and self._printer.is_closed_or_error():
			c = threading.Timer(int(plug[PlugSettings.autoConnectDelay]), self._printer.connect)
			c.daemon = True
			c.start()
		if plug[PlugSettings.sysCmdOn]:
			t = threading.Timer(int(plug[PlugSettings.sysCmdOnDelay]), os.system, args=[plug[PlugSettings.sysRunCmdOn]])
			t.daemon = True
			t.start()
		if self.powerOffWhenIdle is True and plug[PlugSettings.automaticShutdownEnabled] is True:
			self._logger.debug("Resetting idle timer since plug %s was just turned on." % plug_ip)
			self._waitForHeaters = False
			self._reset_idle_timer()

		return status

	def turn_off(self, plug_ip):
		plug = self.plug_search(self._settings.get([TapoSettings.SMART_PLUGS_LIST]), PlugSettings.ip, plug_ip)
		tapo_plug_adapter = TapoPlugAdapter.create_tapo_plug_adapter(plug)

		if plug[PlugSettings.sysCmdOff]:
			t = threading.Timer(
					int(plug[PlugSettings.sysCmdOffDelay]), os.system,
					args=[plug[PlugSettings.sysRunCmdOff]]
			)
			t.daemon = True
			t.start()
		if plug[PlugSettings.autoDisconnect]:
			self._printer.disconnect()
			time.sleep(int(plug[PlugSettings.autoDisconnectDelay]))

		status = tapo_plug_adapter.send_turn_off()
		self._stop_idle_timer()
		return status

	def check_statuses(self):
		for plug in self._settings.get([TapoSettings.SMART_PLUGS_LIST]):
			tapo_plug_adapter = TapoPlugAdapter.create_tapo_plug_adapter(plug)
			status = tapo_plug_adapter.get_status()
			self._plugin_manager.send_plugin_message(self._identifier, status)

	def get_api_commands(self):
		return Commands.get_available_commands()

	def on_api_get(self, request):
		ip = request.args.get(Commands.checkStatus)
		plug = self.plug_search(self._settings.get([TapoSettings.SMART_PLUGS_LIST]), PlugSettings.ip, ip)
		tapo_plug_adapter = TapoPlugAdapter.create_tapo_plug_adapter(plug)
		r = self._api.on_api_get(tapo_plug_adapter, request)
		if r is not None:
			return flask.jsonify(r)

	def on_api_command(self, command, data):
		if not Permissions.PLUGIN_TAPOSMARTPLUG_CONTROL.can():
			return flask.make_response("Insufficient rights", 403)

		tapo_plug_adapter = None
		if PlugSettings.ip in data:
			ip = "{ip}".format(**data)
			plug = self.plug_search(self._settings.get([TapoSettings.SMART_PLUGS_LIST]), PlugSettings.ip, ip)
			tapo_plug_adapter = TapoPlugAdapter.create_tapo_plug_adapter(plug)

		r = self._api.on_api_command(tapo_plug_adapter, command, data)
		if r is not None:
			if command not in Commands.get_auto_shutdown_cmds():
				self._plugin_manager.send_plugin_message(self._identifier, r)
			return flask.jsonify(r)

	def handle_auto_shutdown_cmd(self, tapo: TapoPlugAdapter, command, data):
		if command == Commands.enableAutomaticShutdown:
			self.powerOffWhenIdle = True
			self._reset_idle_timer()
			self.save_power_off()
		elif command == Commands.disableAutomaticShutdown:
			self.powerOffWhenIdle = False
			self._stop_idle_timer()
			if self._abort_timer is not None:
				self._abort_timer.cancel()
				self._abort_timer = None
			self._timeout_value = None
			self.save_power_off()
		elif command == Commands.abortAutomaticShutdown:
			if self._abort_timer is not None:
				self._abort_timer.cancel()
				self._abort_timer = None
			self._timeout_value = None
			# for plug in self._settings.get(["smart_plugs_list"]):
			# 	if plug["useCountdownRules"] and int(plug["countdownOffDelay"]) > 0:
			# 		if "/" in plug[PlugSettings.ip]:
			# 			plug_ip, plug_num = plug[PlugSettings.ip].split("/")
			# 		else:
			# 			plug_ip = plug[PlugSettings.ip]
			# 			plug_num = -1
			# 		self.sendCommand(json.loads('{"count_down":{"delete_all_rules":null}}'), plug_ip, plug_num)
			# 		self._taposmartplug_logger.debug("Cleared countdown rules for %s" % plug[PlugSettings.ip])
			self._taposmartplug_logger.debug("Power off aborted.")
			self._taposmartplug_logger.debug("Restarting idle timer.")
			self._reset_idle_timer()
			self._plugin_manager.send_plugin_message(
					self._identifier, dict(
							powerOffWhenIdle=self.powerOffWhenIdle,
							type="timeout",
							timeout_value=self._timeout_value
					)
			)
		else:
			return dict(ip=data.ip, currentState="unknown")

	def save_power_off(self):
		self._taposmartplug_logger.debug("Automatic power off setting changed: %s" % self.powerOffWhenIdle)
		self._settings.set_boolean([TapoSettings.POWER_OFF_WHEN_IDLE], self.powerOffWhenIdle)
		self._settings.save()
		self._plugin_manager.send_plugin_message(
				self._identifier, dict(
						powerOffWhenIdle=self.powerOffWhenIdle,
						type="timeout",
						timeout_value=self._timeout_value
				)
		)

	# EventHandlerPlugin mixin

	def on_event(self, event, payload):
		# Startup Event

		if event == Events.STARTUP and self._settings.get_boolean([TapoSettings.EVENT_ON_STARTUP_MONITORING]) is True:
			self._taposmartplug_logger.debug("powering on due to %s event." % event)
			for plug in self._settings.get([TapoSettings.SMART_PLUGS_LIST]):
				if plug[PlugSettings.event_on_startup] is True:
					self._taposmartplug_logger.debug("powering on %s due to %s event." % (plug[PlugSettings.ip], event))
					response = self.turn_on(plug[PlugSettings.ip])
					if response[CURRENT_STATE] == ON_STATE:
						self._plugin_manager.send_plugin_message(self._identifier, response)
		# Error Event
		if event == Events.ERROR and self._settings.getBoolean([TapoSettings.EVENT_ON_ERROR_MONITORING]) is True:
			self._taposmartplug_logger.debug("powering off due to %s event." % event)
			for plug in self._settings.get([TapoSettings.SMART_PLUGS_LIST]):
				if plug[PlugSettings.event_on_error] is True:
					self._taposmartplug_logger.debug(
							"powering off %s due to %s event." % (plug[PlugSettings.ip], event)
					)
					response = self.turn_off(plug[PlugSettings.ip])
					if response[CURRENT_STATE] == OFF_STATE:
						self._plugin_manager.send_plugin_message(self._identifier, response)
		# Client Opened Event
		if event == Events.CLIENT_OPENED:
			if self._settings.get_boolean([TapoSettings.POWER_OFF_WHEN_IDLE]):
				self._reset_idle_timer()
			self._plugin_manager.send_plugin_message(
					self._identifier,
					dict(
							powerOffWhenIdle=self.powerOffWhenIdle, type="timeout",
							timeout_value=self._timeout_value
					)
			)
			return
		# Cancelled Print Interpreted Event
		if event == Events.PRINT_FAILED and not self._printer.is_closed_or_error():
			self._taposmartplug_logger.debug("Print cancelled, resetting job_power to 0")
			self.print_job_started = False
			return
		# Print Started Event
		if event == Events.PRINT_STARTED and self._settings.getFloat([TapoSettings.COST_RATE]) > 0:
			self.print_job_started = True
			self._taposmartplug_logger.debug(payload.get("path", None))

		if event == Events.PRINT_STARTED and self.powerOffWhenIdle is True:
			if self._abort_timer is not None:
				self._abort_timer.cancel()
				self._abort_timer = None
				self._taposmartplug_logger.debug("Power off aborted because starting new print.")
			if self._idleTimer is not None:
				self._reset_idle_timer()
			self._timeout_value = None
			self._plugin_manager.send_plugin_message(
					self._identifier,
					dict(
							powerOffWhenIdle=self.powerOffWhenIdle, type="timeout",
							timeout_value=self._timeout_value
					)
			)

		# if event == Events.PRINT_STARTED and self._countdown_active:
		# for plug in self._settings.get(["smart_plugs_list"]):
		# if plug["useCountdownRules"] and int(plug["countdownOffDelay"]) > 0:
		# 	if "/" in plug[PlugSettings.ip]:
		# 		plug_ip, plug_num = plug[PlugSettings.ip].split("/")
		# 	else:
		# 		plug_ip = plug[PlugSettings.ip]
		# 		plug_num = -1
		# 	self.sendCommand(json.loads('{"count_down":{"delete_all_rules":null}}'), plug_ip, plug_num)
		# 	self._taposmartplug_logger.debug("Cleared countdown rules for %s" % plug[PlugSettings.ip])
		# Print Done Event
		if event == Events.PRINT_DONE and self.print_job_started:
			self._taposmartplug_logger.debug(payload)
			# for plug in self._settings.get(["smart_plugs_list"]):
			# 	status = self.check_status(plug[PlugSettings.ip])

			self._storage_interface = self._file_manager._storage(payload.get("origin", "local"))

			self.print_job_started = False

		if self.powerOffWhenIdle and event == Events.MOVIE_RENDERING:
			self._taposmartplug_logger.debug("Timelapse generation started: %s" % payload.get("movie_basename", ""))
			self._timelapse_active = True

		if self._timelapse_active and event == Events.MOVIE_DONE or event == Events.MOVIE_FAILED:
			self._taposmartplug_logger.debug(
					"Timelapse generation finished: %s. Return Code: %s" % (
						payload.get("movie_basename", ""), payload.get("returncode", "completed"))
			)
			self._timelapse_active = False
		# Printer Connected Event
		if event == Events.CONNECTED:
			if self._autostart_file:
				self._taposmartplug_logger.debug("printer connected starting print of %s" % self._autostart_file)
				self._printer.select_file(self._autostart_file, False, printAfterSelect=True)
				self._autostart_file = None
		# File Uploaded Event
		if event == Events.UPLOAD and self._settings.getBoolean(["event_on_upload_monitoring"]):
			if payload.get("print", False):  # implemented in OctoPrint version 1.4.1
				self._taposmartplug_logger.debug(
						"File uploaded: %s. Turning enabled plugs on." % payload.get("name", "")
				)
				self._taposmartplug_logger.debug(payload)
				for plug in self._settings.get([TapoSettings.SMART_PLUGS_LIST]):
					self._taposmartplug_logger.debug(plug)
					if plug[PlugSettings.event_on_upload] is True and not self._printer.is_ready():
						self._taposmartplug_logger.debug(
								"powering on %s due to %s event." % (plug[PlugSettings.ip], event)
						)
						response = self.turn_on(plug[PlugSettings.ip])
						if response[CURRENT_STATE] == ON_STATE:
							self._taposmartplug_logger.debug(
									"power on successful for %s attempting connection in %s seconds" % (
										plug[PlugSettings.ip], plug.get("autoConnectDelay", "0"))
							)
							self._plugin_manager.send_plugin_message(self._identifier, response)
							if payload.get("path", False) and payload.get("target") == "local":
								self._autostart_file = payload.get("path")

	# Idle Timeout

	def _start_idle_timer(self):
		self._stop_idle_timer()

		if self.powerOffWhenIdle:
			self._idleTimer = ResettableTimer(self.idleTimeout * 60, self._idle_poweroff)
			self._idleTimer.daemon = True
			self._idleTimer.start()

	def _stop_idle_timer(self):
		if self._idleTimer:
			self._idleTimer.cancel()
			self._idleTimer = None

	def _reset_idle_timer(self):
		try:
			if self._idleTimer.is_alive():
				self._idleTimer.reset()
			else:
				raise Exception()
		except:
			self._start_idle_timer()

	def _idle_poweroff(self):
		if not self.powerOffWhenIdle:
			return

		if self._waitForHeaters:
			return

		if self._waitForTimelapse:
			return

		if self._printer.is_printing() or self._printer.is_paused():
			return

		if (uptime() / 60) <= (self._settings.get_int([TapoSettings.IDLE_TIMEOUT])):
			self._taposmartplug_logger.debug("Just booted so wait for time sync.")
			self._taposmartplug_logger.debug(
					"uptime: {}, comparison: {}".format(
							(uptime() / 60),
							(self._settings.get_int([TapoSettings.IDLE_TIMEOUT]))
					)
			)
			self._reset_idle_timer()
			return

		self._taposmartplug_logger.debug(
				"Idle timeout reached after %s minute(s). Turning heaters off prior to powering off plugs." % self.idleTimeout
		)
		if self._wait_for_heaters():
			self._taposmartplug_logger.debug("Heaters below temperature.")
			if self._wait_for_timelapse():
				self._timer_start()
		else:
			self._taposmartplug_logger.debug("Aborted power off due to activity.")

	# Timelapse Monitoring

	def _wait_for_timelapse(self):
		self._waitForTimelapse = True
		self._taposmartplug_logger.debug("Checking timelapse status before shutting off power...")

		while True:
			if not self._waitForTimelapse:
				return False

			if not self._timelapse_active:
				self._waitForTimelapse = False
				return True

			self._taposmartplug_logger.debug("Waiting for timelapse before shutting off power...")
			time.sleep(5)

	# Temperature Cooldown

	def _wait_for_heaters(self):
		self._waitForHeaters = True
		heaters = self._printer.get_current_temperatures()

		for heater, entry in heaters.items():
			target = entry.get("target")
			if target is None:
				# heater doesn't exist in fw
				continue

			try:
				temp = float(target)
			except ValueError:
				# not a float for some reason, skip it
				continue

			if temp != 0:
				self._taposmartplug_logger.debug("Turning off heater: %s" % heater)
				self._skipIdleTimer = True
				self._printer.set_temperature(heater, 0)
				self._skipIdleTimer = False
			else:
				self._taposmartplug_logger.debug("Heater %s already off." % heater)

		while True:
			if not self._waitForHeaters:
				return False

			heaters = self._printer.get_current_temperatures()

			highest_temp = 0
			heaters_above_waittemp = []
			for heater, entry in heaters.items():
				if not heater.startswith("tool"):
					continue

				actual = entry.get("actual")
				if actual is None:
					# heater doesn't exist in fw
					continue

				try:
					temp = float(actual)
				except ValueError:
					# not a float for some reason, skip it
					continue

				self._taposmartplug_logger.debug("Heater %s = %sC" % (heater, temp))
				if temp > self.idleTimeoutWaitTemp:
					heaters_above_waittemp.append(heater)

				if temp > highest_temp:
					highest_temp = temp

			if highest_temp <= self.idleTimeoutWaitTemp:
				self._waitForHeaters = False
				return True

			self._taposmartplug_logger.debug(
					"Waiting for heaters(%s) before shutting power off..." % ', '.join(heaters_above_waittemp)
			)
			time.sleep(5)

	# Abort Power Off Timer

	def _timer_start(self):
		if self._abort_timer is not None:
			return

		self._taposmartplug_logger.debug("Starting abort power off timer.")

		self._timeout_value = self.abortTimeout
		self._abort_timer = RepeatedTimer(1, self._timer_task)
		self._abort_timer.start()

	def _timer_task(self):
		if self._timeout_value is None:
			return

		self._timeout_value -= 1
		self._plugin_manager.send_plugin_message(
				self._identifier,
				dict(
						powerOffWhenIdle=self.powerOffWhenIdle, type="timeout",
						timeout_value=self._timeout_value
				)
		)
		if self._timeout_value <= 0:
			if self._abort_timer is not None:
				self._abort_timer.cancel()
				self._abort_timer = None
			self._shutdown_system()

	def _shutdown_system(self):
		self._taposmartplug_logger.debug("Automatically powering off enabled plugs.")
		for plug in self._settings.get([TapoSettings.SMART_PLUGS_LIST]):
			if plug.get(PlugSettings.automaticShutdownEnabled, False):
				response = self.turn_off("{ip}".format(**plug))
				self._plugin_manager.send_plugin_message(self._identifier, response)

	def lookup(self, dic, key, *keys):
		if keys:
			return self.lookup(dic.get(key, {}), *keys)
		return dic.get(key)

	def plug_search(self, list, key, value):
		for item in list:
			if item[key] == value.strip():
				return item

	def encrypt(self, string):
		key = 171
		result = b"\0\0\0" + bytes([len(string)])
		for i in bytes(string.encode('latin-1')):
			a = key ^ i
			key = a
			result += bytes([a])
		return result

	def decrypt(self, string):
		key = 171
		result = b""
		for i in bytes(string):
			a = key ^ i
			key = i
			result += bytes([a])
		return result.decode('latin-1')

	# Gcode processing hook

	def gcode_turn_off(self, plug):
		if plug["warnPrinting"] and self._printer.is_printing():
			self._taposmartplug_logger.debug("Not powering off %s because printer is printing." % plug["label"])
		else:
			chk = self.turn_off(plug[PlugSettings.ip])
			self._plugin_manager.send_plugin_message(self._identifier, chk)

	def gcode_turn_on(self, plug):
		chk = self.turn_on(plug[PlugSettings.ip])
		self._plugin_manager.send_plugin_message(self._identifier, chk)

	def processGCODE(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		if self.powerOffWhenIdle and not (gcode in self._idleIgnoreCommandsArray):
			self._waitForHeaters = False
			self._reset_idle_timer()

		if gcode not in ["M80", "M81"]:
			return

		if gcode == "M80":
			plugip = re.sub(r'^M80\s?', '', cmd)
			self._taposmartplug_logger.debug("Received M80 command, attempting power on of %s." % plugip)
			plug = self.plug_search(self._settings.get([TapoSettings.SMART_PLUGS_LIST]), PlugSettings.ip, plugip)
			self._taposmartplug_logger.debug(plug)
			if plug and plug[PlugSettings.gcodeEnabled]:
				t = threading.Timer(int(plug[PlugSettings.gcodeOnDelay]), self.gcode_turn_on, [plug])
				t.daemon = True
				t.start()
			return
		if gcode == "M81":
			plugip = re.sub(r'^M81\s?', '', cmd)
			self._taposmartplug_logger.debug("Received M81 command, attempting power off of %s." % plugip)
			plug = self.plug_search(self._settings.get([TapoSettings.SMART_PLUGS_LIST]), PlugSettings.ip, plugip)
			self._taposmartplug_logger.debug(plug)
			if plug and plug[PlugSettings.gcodeEnabled]:
				t = threading.Timer(int(plug[PlugSettings.gcodeOffDelay]), self.gcode_turn_off, [plug])
				t.daemon = True
				t.start()
			return

	def process_at_command(self, comm_instance, phase, command, parameters, tags=None, *args, **kwargs):
		self._logger.info(command)
		self._logger.info(parameters)
		if command == "TAPOON":
			plugip = parameters
			self._taposmartplug_logger.debug("Received @TAPOON command, attempting power on of %s." % plugip)
			plug = self.plug_search(self._settings.get([TapoSettings.SMART_PLUGS_LIST]), PlugSettings.ip, plugip)
			self._taposmartplug_logger.debug(plug)
			if plug and plug[PlugSettings.gcodeEnabled]:
				t = threading.Timer(int(plug[PlugSettings.gcodeOnDelay]), self.gcode_turn_on, [plug])
				t.daemon = True
				t.start()
			return None
		if command == "TAPOOFF":
			plugip = parameters
			self._taposmartplug_logger.debug("Received TAPOOFF command, attempting power off of %s." % plugip)
			plug = self.plug_search(self._settings.get([TapoSettings.SMART_PLUGS_LIST]), PlugSettings.ip, plugip)
			self._taposmartplug_logger.debug(plug)
			if plug and plug[PlugSettings.gcodeEnabled]:
				t = threading.Timer(int(plug[PlugSettings.gcodeOffDelay]), self.gcode_turn_off, [plug])
				t.daemon = True
				t.start()
			return None
		if command == 'TAPOIDLEON':
			self.powerOffWhenIdle = True
			self._reset_idle_timer()
		if command == 'TAPOIDLEOFF':
			self.powerOffWhenIdle = False
			self._stop_idle_timer()
			if self._abort_timer is not None:
				self._abort_timer.cancel()
				self._abort_timer = None
			self._timeout_value = None
		if command in ["TAPOIDLEON", "TAPOIDLEOFF"]:
			self._plugin_manager.send_plugin_message(
					self._identifier,
					dict(
							powerOffWhenIdle=self.powerOffWhenIdle, type="timeout",
							timeout_value=self._timeout_value
					)
			)

	# Temperatures received hook

	def check_temps(self, parsed_temps):
		thermal_runaway_triggered = False
		for k, v in parsed_temps.items():
			if k == "B" and v[0] > int(self._settings.get([TapoSettings.THERMAL_RUNAWAY_MAX_BED])):
				self._taposmartplug_logger.debug("Max bed temp reached, shutting off plugs.")
				thermal_runaway_triggered = True
			if k.startswith("T") and v[0] > int(self._settings.get([TapoSettings.THERMAL_RUNAWAY_MAX_EXTRUDER])):
				self._taposmartplug_logger.debug("Extruder max temp reached, shutting off plugs.")
				thermal_runaway_triggered = True
			if thermal_runaway_triggered:
				for plug in self._settings.get([TapoSettings.SMART_PLUGS_LIST]):
					if plug[PlugSettings.thermal_runaway]:
						response = self.turn_off(plug[PlugSettings.ip])
						if response[CURRENT_STATE] == OFF_STATE:
							self._plugin_manager.send_plugin_message(self._identifier, response)

	def monitor_temperatures(self, comm, parsed_temps):
		if self._settings.get([TapoSettings.THERMAL_RUNAWAY_MONITORING]):
			# Run inside it's own thread to prevent communication blocking
			t = threading.Timer(0, self.check_temps, [parsed_temps])
			t.daemon = True
			t.start()
		return parsed_temps

	# Access Permissions Hook

	@staticmethod
	def get_additional_permissions():
		return [
			dict(
					key="CONTROL",
					name="Control Plugs",
					description=gettext("Allows control of configured plugs."),
					roles=["admin"],
					dangerous=True,
					default_groups=[ADMIN_GROUP]
			)
		]

	##~~ Softwareupdate hook

	def get_update_information(self):
		return dict(
				taposmartplug=dict(
						displayName="Tapo Smartplug",
						displayVersion=self._plugin_version,
						type="github_release",
						user="Tyfy",
						repo="OctoPrint-TapoSmartplug",
						current=self._plugin_version,
						stable_branch=dict(
								name="Stable", branch="master", comittish=["master"]
						),
						prerelease_branches=[
							dict(
									name="Release Candidate",
									branch="rc",
									comittish=["rc", "master"],
							)
						],
						pip="https://github.com/Tyfy/OctoPrint-TapoSmartplug/archive/{target_version}.zip"
				)
		)




__plugin_name__ = "Tapo Smartplug"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = taposmartplugPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.comm.protocol.gcode.queuing":         __plugin_implementation__.processGCODE,
		"octoprint.comm.protocol.atcommand.sending":     __plugin_implementation__.process_at_command,
		"octoprint.comm.protocol.temperatures.received": __plugin_implementation__.monitor_temperatures,
		"octoprint.access.permissions":                  __plugin_implementation__.get_additional_permissions,
		"octoprint.plugin.softwareupdate.check_config":  __plugin_implementation__.get_update_information
	}
