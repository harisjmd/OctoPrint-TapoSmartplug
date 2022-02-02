import logging
import threading
from .utils import encode_string


class TapoSettings:
	DEBUG_LOGGING = "debug_logging"
	arrSmartplugs = "arrSmartplugs"
	SMART_PLUGS_LIST = "smart_plugs_list"
	pollinginterval = "pollingInterval"
	POLLING_INTERVAL = "polling_interval"
	pollingEnabled = "pollingEnabled"
	POLLING_ENABLED = "polling_enabled"
	THERMAL_RUNAWAY_MONITORING = "thermal_runaway_monitoring"
	THERMAL_RUNAWAY_MAX_BED = "thermal_runaway_max_bed"
	THERMAL_RUNAWAY_MAX_EXTRUDER = "thermal_runaway_max_extruder"
	EVENT_ON_ERROR_MONITORING = "event_on_error_monitoring"
	EVENT_ON_DISCONNECT_MONITORING = "event_on_disconnect_monitoring"
	EVENT_ON_UPLOAD_MONITORING = "event_on_upload_monitoring"
	EVENT_ON_STARTUP_MONITORING = "event_on_startup_monitoring"
	COST_RATE = "cost_rate"
	abortTimeout = "abortTimeout"
	ABORT_TIMEOUT = "abort_timeout"
	powerOffWhenIdle = "powerOffWhenIdle"
	POWER_OFF_WHEN_IDLE = "power_off_when_idle"
	idleTimeout = "idleTimeout"
	IDLE_TIMEOUT = "idle_timeout"
	idleIgnoreCommands = "idleIgnoreCommands"
	IDLE_IGNORE_COMMANDS = "idle_ignore_commands"
	idleTimeoutWaitTemp = "idleTimeoutWaitTemp"
	IDLE_TIMEOUT_WAIT_TEMP = "idle_timeout_wait_temp"
	PROGRESS_POLLING = "progress_polling"

	@staticmethod
	def get_default_settings():
		return {
			TapoSettings.DEBUG_LOGGING:                  False,
			TapoSettings.SMART_PLUGS_LIST:               [],
			TapoSettings.POLLING_INTERVAL:               15,
			TapoSettings.POLLING_ENABLED:                False,
			TapoSettings.THERMAL_RUNAWAY_MONITORING:     False,
			TapoSettings.THERMAL_RUNAWAY_MAX_BED:        0,
			TapoSettings.THERMAL_RUNAWAY_MAX_EXTRUDER:   0,
			TapoSettings.EVENT_ON_ERROR_MONITORING:      False,
			TapoSettings.EVENT_ON_DISCONNECT_MONITORING: False,
			TapoSettings.EVENT_ON_UPLOAD_MONITORING:     False,
			TapoSettings.EVENT_ON_STARTUP_MONITORING:    False,
			TapoSettings.COST_RATE:                      0,
			TapoSettings.ABORT_TIMEOUT:                  30,
			TapoSettings.POWER_OFF_WHEN_IDLE:            False,
			TapoSettings.IDLE_TIMEOUT:                   30,
			TapoSettings.IDLE_IGNORE_COMMANDS:           'M105',
			TapoSettings.IDLE_TIMEOUT_WAIT_TEMP:         50,
			TapoSettings.PROGRESS_POLLING:               False
		}

	@staticmethod
	def get_version():
		return 14

	def on_settings_migrate(self, logger, settings, target, current=None):
		if logger is None:
			logger = logging.getLogger("octoprint.plugins.taposmartplug.TapoSettings")
		if current is None or current < 5:
			# Reset plug settings to defaults.
			logger.debug("Resetting {0} for taposmartplug settings.".format(TapoSettings.arrSmartplugs))
			settings.set([TapoSettings.arrSmartplugs], self.get_default_settings()[TapoSettings.SMART_PLUGS_LIST])
		elif current == 6:
			# Loop through plug array and set emeter to None
			arrSmartplugs_new = []
			for plug in settings.get([TapoSettings.arrSmartplugs]):
				plug[PlugSettings.emeter] = None
				arrSmartplugs_new.append(plug)

			logger.info("Updating plug array, converting")
			logger.info(settings.get([TapoSettings.arrSmartplugs]))
			logger.info("to")
			logger.info(arrSmartplugs_new)
			settings.set([TapoSettings.arrSmartplugs], arrSmartplugs_new)
		elif current == 7:
			# Loop through plug array and set emeter to None
			arrSmartplugs_new = []
			for plug in settings.get([TapoSettings.arrSmartplugs]):
				plug[PlugSettings.emeter] = dict(get_realtime=False)
				arrSmartplugs_new.append(plug)

			logger.info("Updating plug array, converting")
			logger.info(settings.get([TapoSettings.arrSmartplugs]))
			logger.info("to")
			logger.info(arrSmartplugs_new)
			settings.set([TapoSettings.arrSmartplugs], arrSmartplugs_new)

		if current is not None and current < 9:
			arrSmartplugs_new = []
			for plug in settings.get([TapoSettings.arrSmartplugs]):
				plug[PlugSettings.thermal_runaway] = False
				arrSmartplugs_new.append(plug)
			settings.set([TapoSettings.arrSmartplugs], arrSmartplugs_new)

		if current is not None and current < 10:
			arrSmartplugs_new = []
			for plug in settings.get([TapoSettings.arrSmartplugs]):
				plug[PlugSettings.event_on_error] = False
				plug[PlugSettings.event_on_disconnect] = False
				arrSmartplugs_new.append(plug)
			settings.set([TapoSettings.arrSmartplugs], arrSmartplugs_new)

		if current is not None and current < 11:
			arrSmartplugs_new = []
			for plug in settings.get([TapoSettings.arrSmartplugs]):
				plug[PlugSettings.automaticShutdownEnabled] = False
				arrSmartplugs_new.append(plug)
			settings.set([TapoSettings.arrSmartplugs], arrSmartplugs_new)

		if current is not None and current < 12:
			arrSmartplugs_new = []
			for plug in settings.get([TapoSettings.arrSmartplugs]):
				plug[PlugSettings.event_on_upload] = False
				arrSmartplugs_new.append(plug)
			settings.set([TapoSettings.arrSmartplugs], arrSmartplugs_new)

		if current is not None and current < 13:
			arrSmartplugs_new = []
			for plug in settings.get([TapoSettings.arrSmartplugs]):
				plug[PlugSettings.event_on_startup] = False
				arrSmartplugs_new.append(plug)
			settings.set([TapoSettings.arrSmartplugs], arrSmartplugs_new)
		if current is not None and current < 14:
			arrSmartplugs_new = []
			for plug in settings.get([TapoSettings.arrSmartplugs]):
				plug[PlugSettings.event_on_startup] = False
				plug[PlugSettings.password] = encode_string(plug[PlugSettings.password])
				arrSmartplugs_new.append(plug)
			settings.set([TapoSettings.SMART_PLUGS_LIST], arrSmartplugs_new)
			settings.set([TapoSettings.POLLING_ENABLED], settings.get([TapoSettings.pollingEnabled]))
			settings.set([TapoSettings.POLLING_INTERVAL], settings.get([TapoSettings.pollinginterval]))
			settings.set([TapoSettings.ABORT_TIMEOUT], settings.get([TapoSettings.abortTimeout]))
			settings.set([TapoSettings.POWER_OFF_WHEN_IDLE], settings.get([TapoSettings.powerOffWhenIdle]))
			settings.set([TapoSettings.IDLE_TIMEOUT], settings.get([TapoSettings.idleTimeout]))
			settings.set([TapoSettings.IDLE_IGNORE_COMMANDS], settings.get([TapoSettings.idleIgnoreCommands]))
			settings.set([TapoSettings.IDLE_TIMEOUT_WAIT_TEMP], settings.get([TapoSettings.idleTimeoutWaitTemp]))


class ResettableTimer(threading.Thread):
	def __init__(self, interval, function, args=None, kwargs=None, on_reset=None, on_cancelled=None):
		threading.Thread.__init__(self)
		self._event = threading.Event()
		self._mutex = threading.Lock()
		self.is_reset = True

		if args is None:
			args = []
		if kwargs is None:
			kwargs = dict()

		self.interval = interval
		self.function = function
		self.args = args
		self.kwargs = kwargs
		self.on_cancelled = on_cancelled
		self.on_reset = on_reset

	def run(self):
		while self.is_reset:
			with self._mutex:
				self.is_reset = False
			self._event.wait(self.interval)

		if not self._event.isSet():
			self.function(*self.args, **self.kwargs)
		with self._mutex:
			self._event.set()

	def cancel(self):
		with self._mutex:
			self._event.set()

		if callable(self.on_cancelled):
			self.on_cancelled()

	def reset(self, interval=None):
		with self._mutex:
			if interval:
				self.interval = interval

			self.is_reset = True
			self._event.set()
			self._event.clear()

		if callable(self.on_reset):
			self.on_reset()


class PlugSettings:
	autoConnect = "autoConnect"
	autoConnectDelay = "autoConnectDelay"
	autoDisconnect = "autoDisconnect"
	autoDisconnectDelay = "autoDisconnectDelay"
	automaticShutdownEnabled = "automaticShutdownEnabled"
	btnColor = "btnColor"
	countdownOffDelay = "countdownOffDelay"
	countdownOnDelay = "countdownOnDelay"
	currentState = "currentState"
	displayWarning = "displayWarning"
	emeter = "emeter"
	get_realtime = "get_realtime"
	event_on_disconnect = "event_on_disconnect"
	event_on_error = "event_on_error"
	event_on_startup = "event_on_startup"
	event_on_upload = "event_on_upload"
	gcodeEnabled = "gcodeEnabled"
	gcodeOffDelay = "gcodeOffDelay"
	gcodeOnDelay = "gcodeOnDelay"
	icon = "icon"
	ip = "ip"
	label = "label"
	password = "password"
	sysCmdOff = "sysCmdOff"
	sysCmdOffDelay = "sysCmdOffDelay"
	sysCmdOn = "sysCmdOn"
	sysCmdOnDelay = "sysCmdOnDelay"
	sysRunCmdOff = "sysRunCmdOff"
	sysRunCmdOn = "sysRunCmdOn"
	thermal_runaway = "thermal_runaway"
	useCountdownRules = "useCountdownRules"
	username = "username"
	warnPrinting = "warnPrinting"
