-- Omarchy session restore: relaunch saved windows after login.
-- Add to ~/.config/hypr/autostart.lua (loaded after defaults) or merge into
-- the shipped default/hypr/autostart.lua alongside the post-boot hook.
hl.exec_cmd("sleep 2 && omarchy-sesh restore")