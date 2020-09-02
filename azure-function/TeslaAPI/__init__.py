import logging
import os
import json
import requests
import azure.functions as func

from typing import Optional
from pydantic import BaseModel, ValidationError
from telegram import Bot

logging.basicConfig(level=logging.INFO)

TESLA_API_BASE = "https://owner-api.teslamotors.com/api/1/vehicles"
TELEGRAM_CONFIG = os.path.join(os.path.dirname(os.path.realpath(__file__)), "telegram_config.json")
TELEGRAM_BOT = None
TELEGRAM_CHAT_ID = None
COMMAND_ADAPTER = {
    "wake_up": "wake_up",
    "stop_hvac": "auto_conditioning_stop",
    "start_hvac": "auto_conditioning_start",
    "start_hvac_max": "set_preconditioning_max",
    "set_temps": "set_temps",
    "honk_horn": "honk_horn",
    "flash_lights": "flash_lights",
    "actuate_trunk": "actuate_trunk",
    "actuate_frunk": "actuate_trunk",
    "start_remote_drive": "remote_start_drive",
    "start_sentry": "set_sentry_mode",
    "stop_sentry": "set_sentry_mode",
    "start_valet_mode": "set_valet_mode",
    "stop_valet_mode": "set_valet_mode",
    "unlock_doors": "door_unlock",
    "lock_doors": "door_lock",
    "open_charge_port_door": "charge_port_door_open",
    "close_charge_port_door": "charge_port_door_close",
    "start_charging": "charge_start",
    "stop_charging": "charge_stop",
    "set_charge_limit": "set_charge_limit",
    "charge_standard": "charge_standard",
    "charge_max_range": "charge_max_range",
    "close_windows": "window_control",
    "vent_windows": "window_control"
}


class RequestModel(BaseModel):
    TOKEN: str
    VEHICLE_ID: str
    INPUT_CMD: str
    VEHICLE_TEMP: Optional[str] = None
    VEHICLE_CHARGE_LIMIT: Optional[str] = None


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')

    if req.method == "GET":
        return respond("Tesla API Relay running successfully")
    elif req.method == "POST":
        try:
            setup_telegram()
            return parse_post_request(req.get_json())
        except Exception as e:
            logging.exception(e)
            return respond("Server error %s" % str(e), status_code=500)
    else:
        return respond("Unsupported method %s" % req.method, status_code=400)


def parse_post_request(body: str):
    try:
        model = RequestModel.parse_obj(body)
    except ValidationError as e:
        logging.exception(e)
        return respond("Validation error %s" % e, status_code=400)

    if model.INPUT_CMD not in COMMAND_ADAPTER:
        return respond("Unknown command %s" % model.INPUT_CMD, status_code=400, command=model.INPUT_CMD)

    command_translated = COMMAND_ADAPTER[model.INPUT_CMD]
    logging.info(os.path.join(TESLA_API_BASE, model.VEHICLE_ID, "command", command_translated))
    logging.info(gather_body_params(model.INPUT_CMD, command_translated, model))
    resp = requests.post(os.path.join(TESLA_API_BASE, model.VEHICLE_ID, "command", command_translated),
                         json=gather_body_params(model.INPUT_CMD, command_translated, model),
                         headers={"Authorization": "Bearer %s" % model.TOKEN})

    response_content = resp.json()
    if not resp.ok:
        if resp.status_code == 401:
            return respond("Unauthorized. Access Token or Vehicle ID seems to be wrong!",
                           status_code=401, command=model.INPUT_CMD)
        else:
            return respond({"Tesla API error": response_content}, status_code=502, command=model.INPUT_CMD)
    else:
        if "response" in response_content and not response_content["response"]["result"]:
            return respond({"Tesla API error": response_content}, status_code=502, command=model.INPUT_CMD)
        else:
            return respond("Command %s executed successfully" % model.INPUT_CMD, command=model.INPUT_CMD)


def gather_body_params(command: str, command_translated: str, model: RequestModel):
    if command_translated == "actuate_trunk":
        if command == "actuate_frunk":
            return {"which_trunk": "front"}
        elif command == "actuate_trunk":
            return {"which_trunk": "rear"}

    if command_translated == "set_sentry_mode":
        if command == "start_sentry":
            return {"on": True}
        elif command == "stop_sentry":
            return {"on": False}

    if command_translated == "set_valet_mode":
        if command == "start_valet_mode":
            return {"on": True}
        elif command == "stop_valet_mode":
            return {"on": False}

    if command_translated == "window_control":
        if command == "close_windows":
            return {"command": "close", "lat": 0, "lon": 0}
        elif command == "vent_windows":
            return {"command": "vent", "lat": 0, "lon": 0}

    if command_translated == "set_temps":
        return {"driver_temp": model.VEHICLE_TEMP}

    if command_translated == "set_charge_limit":
        return {"percent": model.VEHICLE_CHARGE_LIMIT}

    return {}


def setup_telegram():
    global TELEGRAM_BOT, TELEGRAM_CHAT_ID
    if os.path.exists(TELEGRAM_CONFIG):
        with open(TELEGRAM_CONFIG) as json_file:
            data = json.load(json_file)
            TELEGRAM_BOT = Bot(token=data["token"])
            TELEGRAM_CHAT_ID = data["chatId"]


def respond(message, command: str = "", status_code: int = 200):
    response = {"command": command, "msg": message, "statusCode": status_code}

    if TELEGRAM_BOT is not None and status_code != 200:
        if "Tesla API error" in message and "error" in message["Tesla API error"] and \
                "vehicle unavailable" in message["Tesla API error"]["error"]:

            telegram_message = "Couldn't execute the command `%s` because your Tesla seems to be asleep." % command
        else:
            telegram_message = response

        TELEGRAM_BOT.send_message(text=json.dumps(telegram_message), chat_id=TELEGRAM_CHAT_ID)

    return func.HttpResponse(json.dumps(response),
                             status_code=status_code,
                             headers={"Content-Type": "application/json"})
