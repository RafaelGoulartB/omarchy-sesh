# Omarchy Session Restoration

This context captures the terms used to preserve and restore a desktop session.
It keeps restore behavior distinct from application-owned state.

## Language

**Snapshot**:
A saved description of one desktop session, including its windows and saved
workspace layout, that may be selected for restoration.
_Avoid_: backup, session file

**Restore run**:
A single attempt to apply one saved session to the current Hyprland desktop,
from matching existing windows through launch, placement, correction, and final
verification.
_Avoid_: restore process, restore flow
