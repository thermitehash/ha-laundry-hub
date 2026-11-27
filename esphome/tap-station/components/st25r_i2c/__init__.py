"""ST25R3916 over I2C: reuses ESPHome's st25r reader with an I2C transport."""

import esphome.codegen as cg
from esphome.components import i2c, st25r
import esphome.config_validation as cv
from esphome.const import CONF_ID

AUTO_LOAD = ["st25r"]
CODEOWNERS = ["@thermitehash"]
DEPENDENCIES = ["i2c"]
MULTI_CONF = True

CONF_MAX_BACKOFF = "max_backoff"

st25r_i2c_ns = cg.esphome_ns.namespace("st25r_i2c")
ST25RI2C = st25r_i2c_ns.class_("ST25RI2C", st25r.ST25R, i2c.I2CDevice)

CONFIG_SCHEMA = (
    st25r.ST25R_SCHEMA.extend(
        {
            cv.GenerateID(): cv.declare_id(ST25RI2C),
            cv.Optional(CONF_MAX_BACKOFF, default="60s"): cv.All(
                cv.positive_time_period_milliseconds,
                cv.Range(min=cv.TimePeriod(seconds=1)),
            ),
        }
    )
    # Unit NFC (U216) answers at 0x50.
    .extend(i2c.i2c_device_schema(0x50))
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await i2c.register_i2c_device(var, config)
    await st25r.setup_st25r(var, config)
    cg.add(var.set_max_backoff(config[CONF_MAX_BACKOFF]))
