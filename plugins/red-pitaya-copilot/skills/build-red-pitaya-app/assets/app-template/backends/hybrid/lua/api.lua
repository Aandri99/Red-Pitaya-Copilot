local cjson = require "cjson"

local SLUG = "__APP_SLUG__"
local URI = ngx.var.uri or ""
local ACTION = string.match(URI, "^/" .. SLUG .. "/api/([%w_%-]+)$")

local function error_reply(status_code, code, message)
    ngx.status = status_code
    ngx.header.content_type = "application/json; charset=utf-8"
    ngx.header["Cache-Control"] = "no-store"
    ngx.say(cjson.encode({
        ok = false,
        error = { code = code, message = message }
    }))
end

if ACTION ~= "status" and ACTION ~= "version" and ACTION ~= "stop" then
    error_reply(404, "unknown_action", "Unknown API action")
    return
end

local expected_method = ACTION == "stop" and "POST" or "GET"
if ngx.req.get_method() ~= expected_method then
    error_reply(405, "method_not_allowed", "Wrong method for action")
    return
end

local result = ngx.location.capture("/" .. SLUG .. "/internal/service/" .. ACTION)
if result == nil then
    error_reply(502, "service_unavailable", "Service request failed")
    return
end

ngx.status = result.status
ngx.header.content_type = "application/json; charset=utf-8"
ngx.header["Cache-Control"] = "no-store"
ngx.print(result.body)
