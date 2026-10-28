local cjson = require "cjson"

local SLUG = "__APP_SLUG__"
local VERSION = "__APP_VERSION__"
local URI = ngx.var.uri or ""
local ACTION = string.match(URI, "^/" .. SLUG .. "/api/([%w_%-]+)$")

local function reply(status_code, payload)
    ngx.status = status_code
    ngx.header.content_type = "application/json; charset=utf-8"
    ngx.header["Cache-Control"] = "no-store"
    ngx.say(cjson.encode(payload))
end

local function error_reply(status_code, code, message)
    reply(status_code, {
        ok = false,
        error = { code = code, message = message }
    })
end

local function status_payload()
    return {
        service = "integration_required",
        hardware_ready = false,
        safe_state = cjson.null,
        stop_implemented = false,
        version = VERSION,
        server_time = ngx.localtime()
    }
end

if ACTION == nil then
    error_reply(404, "unknown_action", "Unknown API action")
    return
end

if ACTION == "status" or ACTION == "version" then
    if ngx.req.get_method() ~= "GET" then
        error_reply(405, "method_not_allowed", "Use GET for this action")
        return
    end
    reply(200, { ok = true, status = status_payload() })
    return
end

if ACTION == "stop" then
    if ngx.req.get_method() ~= "POST" then
        error_reply(405, "method_not_allowed", "Use POST for stop")
        return
    end
    -- SCAFFOLD_HARDWARE_INTEGRATION_REQUIRED
    -- Replace this rejection only after implementing and testing a fixed,
    -- validated call to the single hardware resource owner.
    error_reply(501, "integration_required",
        "Safe-stop hardware integration is not implemented")
    return
end

error_reply(404, "unknown_action", "Unknown API action")
