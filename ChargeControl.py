import json
import threading
import traceback
from copy import copy
from datetime import datetime, timedelta
from hashlib import md5
from time import sleep
from MyPSACC import MyPSACC
from MyLogger import logger
from psa_connectedcar.rest import ApiException

class ChargeControls:

    def __init__(self):
        self.list: dict = {}
        self._confighash = None

    def save_config(self, name="charge_config.json", force=False):
        chd = {}
        for key, el in self.list.items():
            chd[el.vin] =  {"percentage_threshold": el.percentage_threshold, "stop_hour": el._stop_hour}
        config_str = json.dumps(chd, sort_keys=True, indent=4).encode('utf-8')
        new_hash = md5(config_str).hexdigest()
        if force or self._confighash != new_hash :
            with open(name, "wb") as f:
                f.write(config_str)
            self._confighash = new_hash
            logger.info("save config change")

    def load_config(psacc:MyPSACC, name="charge_config.json"):
        with open(name, "r") as f:
            str = f.read()
            chd =  json.loads(str)
            charge_control_list = ChargeControls()
            for vin, el in chd.items():
                charge_control_list.list[vin] = ChargeControl(psacc,vin,**el)
            return charge_control_list
    def get(self,vin):
        try:
            return self.list[vin]
        except KeyError:
            return None

    def start(self):
        for vin, charge_control in self.list.items():
            charge_control.start()


class ChargeControl:
    periodicity = 120
    MQTT_TIMEOUT = 60
    def __init__(self, psacc:MyPSACC, vin, percentage_threshold, stop_hour):
        self.vin = vin
        self.percentage_threshold = percentage_threshold
        self.set_stop_hour(stop_hour)
        self.psacc = psacc
        self.retry_count = 0
        self.thread:threading.Timer = None

    def set_stop_hour(self,stop_hour):
        if stop_hour is None or stop_hour == [0, 0]:
            self._stop_hour = None
            self._next_stop_hour = None
        else:
            self._stop_hour = stop_hour
            self._next_stop_hour = datetime.now().replace(hour=stop_hour[0], minute=stop_hour[1], second=0)
            if self._next_stop_hour  < datetime.now():
                self._next_stop_hour += timedelta(days=1)

    def start(self):
        periodicity = ChargeControl.periodicity
        now = datetime.now()
        if self._next_stop_hour is not None and self._next_stop_hour < now:
            stop_charge = True
            self._next_stop_hour += timedelta(days=1)
            logger.info("it's time to stop the charge")
        else :
            stop_charge = False

        if self.percentage_threshold != 100 or stop_charge:
            res = None
            try:
                res = self.psacc.get_vehicle_info(self.vin)
            except ApiException:
                logger.error(traceback.format_exc())
            if res is not None:
                status = res.energy[0]['charging']['status']
                level = res.energy[0]["level"]
                logger.info(f"charging status of {self.vin} is {status}, battery level: {level}")
                if status == "InProgress":
                    if (level >= self.percentage_threshold and self.retry_count < 2) or stop_charge:
                        self.psacc.charge_now(self.vin,False)
                        self.retry_count += 1
                        sleep(ChargeControl.MQTT_TIMEOUT)
                        res = self.psacc.get_vehicle_info(self.vin)
                        status = res.energy[0]['charging']['status']
                        if status == "InProgress":
                            logger.warn(f"retry to stop the charge of {self.vin}")
                            self.psacc.charge_now(self.vin, False)
                            self.retry_count += 1
                    if self._next_stop_hour is not None:
                        next_in_second = (self._next_stop_hour- now).total_seconds()
                        if next_in_second < periodicity:
                            periodicity = next_in_second
                else:
                    self.retry_count = 0
            else:
                logger.error(f"error when get vehicle info of {self.vin}")
        self.thread = threading.Timer(periodicity, self.start)
        self.thread.start()

    def get_dict(self):
        chd = copy(self.__dict__)
        chd.pop("psacc")
        chd.pop("thread")
        return chd