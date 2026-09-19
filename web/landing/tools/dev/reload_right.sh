#!/bin/sh
# Reload the GITRL tab on the right monitor in the user's real Chrome, then give
# focus back to whatever app was frontmost. Finds the window by title, not id.
hs -t 10 -c '
local prev = hs.application.frontmostApplication()
local chrome
for _, a in ipairs(hs.application.applicationsForBundleID("com.google.Chrome")) do
  for _, w in ipairs(a:allWindows()) do
    if (w:title() or ""):find("GITRL", 1, true) or (w:title() or ""):find("GITSPACE", 1, true) then chrome = w end
  end
end
if not chrome then return "no GITRL window" end
chrome:focus()
hs.timer.usleep(150000)
hs.eventtap.keyStroke({"cmd"}, "r", 0)
hs.timer.usleep(150000)
if prev and prev:bundleID() ~= "com.google.Chrome" then prev:activate() end
return "reloaded " .. chrome:title():sub(1, 40) .. " on " .. chrome:screen():name()'
