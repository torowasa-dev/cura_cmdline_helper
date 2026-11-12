; Standalone filament pullback - Marlin-compatible single-extruder printer
; Run directly from the printer's SD card. Do not slice this file.
; Target: 220 C. Pullback: 50 mm. Requested speed: 20 mm/s.
; No homing or XYZ movement commands.

G21                 ; Use millimeters
M140 S0             ; Bed heater off
M109 R220           ; Set hotend to 220 C and wait for heating or cooling
M83                 ; Relative extruder positioning
G1 E-50 F1200       ; Retract 50 mm at 1200 mm/min (20 mm/s)
M400                ; Wait until retraction is complete
M104 S0             ; Hotend heater off
G92 E0              ; Reset the extruder coordinate without moving
M82                 ; Restore absolute extruder positioning
M84 E               ; Release the extruder motor
