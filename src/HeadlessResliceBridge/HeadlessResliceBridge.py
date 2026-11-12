"""Run one Cura 5.12.0 project through Cura's own reader, backend and writer.

This module is loaded by Cura, never by the caller's system Python. It relies
on a few explicitly checked Cura 5.12.0 internals; use the matching launcher.
The launcher installs it only into a disposable copy of the Cura user data.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
import zipfile

from PyQt6.QtCore import QCoreApplication, QTimer

from UM.Backend.Backend import BackendState
from UM.Extension import Extension
from UM.Logger import Logger
from UM.Math.AxisAlignedBox import AxisAlignedBox
from UM.Math.Vector import Vector
from UM.Mesh.MeshWriter import MeshWriter
from UM.Operations.AddSceneNodeOperation import AddSceneNodeOperation
from UM.OutputDevice.OutputDevice import OutputDevice
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Workspace.WorkspaceReader import WorkspaceReader


class HeadlessResliceBridge(Extension):
    """One manifest, one isolated Cura process, one output file."""

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._done = False
        self._phase = "initializing"
        self._started_at = time.monotonic()
        self._manifest = {}
        self._result_path = None
        self._reader = None
        self._backend = None
        self._scene_loaded = False
        self._validation_seen = False
        self._backend_done = False
        self._output_created = False
        self._summary = {}
        self._post_errors = []
        self._post_originals = []
        self._target = None
        self._insert_before_end = None
        self._target_snapshot = None
        self._target_applied = False
        self._source_printer = None
        self._object_assignments = []
        self._timer = QTimer(app)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._poll_safely)
        try:
            path = Path(os.environ["CURA_HEADLESS_RESLICE_JOB"])
            self._manifest = json.loads(path.read_text(encoding="utf-8"))
            self._result_path = Path(self._manifest["result_path"])
            self._action = self._manifest.get("action", "slice")
            if self._action not in ("slice", "list_printers"):
                raise ValueError("Unknown bridge action: " + str(self._action))
            if self._action == "slice":
                self._input = Path(self._manifest["input_path"]).resolve(strict=True)
                self._output = Path(self._manifest["output_path"]).absolute()
                if self._input == self._output.resolve():
                    raise ValueError("Input and output must be different files")
                insert_path = self._manifest.get("insert_before_end_path")
                if insert_path:
                    self._insert_before_end = self._load_insert_gcode(Path(insert_path))
            self._timeout = float(self._manifest["timeout_seconds"])
            if self._timeout <= 0:
                raise ValueError("timeout_seconds must be positive")
            self._old_excepthook = sys.excepthook
            sys.excepthook = self._uncaught_exception
            app.initializationFinished.connect(self._schedule_start)
        except Exception as exc:
            self._fail("Bridge initialization failed: " + str(exc))

    def _schedule_start(self):
        # Other initializationFinished handlers initialize CuraEngineBackend.
        QTimer.singleShot(0, self._start_safely)

    def _start_safely(self):
        if self._done:
            return
        try:
            self._start()
        except Exception as exc:
            self._fail("Project initialization failed: " + str(exc))

    def _start(self):
        actual = self._app.getVersion()
        expected = self._manifest.get("expected_version", "5.12.0")
        if actual != expected or expected != "5.12.0":
            raise RuntimeError("This bridge requires Cura 5.12.0; found " + str(actual))
        if not self._app.getIsHeadLess():
            raise RuntimeError("Cura must be started with --headless")
        self._app.enableSave(False)
        self._disable_telemetry()
        self._app.getPreferences().setValue("general/auto_slice", False)
        self._backend = self._app.getBackend()
        if self._action == "list_printers":
            printers = [self._printer_info(stack) for stack in self._saved_printers()]
            self._finish({"status": "success", "success": True, "ok": True,
                          "printers": printers, "cura_version": actual}, 0)
            return
        selector = self._manifest.get("printer")
        if selector is not None:
            self._select_saved_target(selector)
        for attr in ("forceSlice", "disableTimer", "backendDone", "backendStateChange",
                     "_slicing", "_build_plates_to_be_sliced"):
            if not hasattr(self._backend, attr):
                raise RuntimeError("Unsupported backend API: " + attr)
        self._backend.disableTimer()
        self._backend.backendDone.connect(self._on_backend_done)
        self._backend.backendStateChange.connect(self._on_backend_state)
        self._backend.backendError.connect(self._on_backend_error)
        checker = self._app.getMachineErrorChecker()
        checker.errorCheckFinished.connect(self._on_validation_finished)
        checker.needToWaitForResultChanged.connect(self._on_validation_wait_changed)
        self._timer.start()

        # Capture requested post-processing before any plugin can rewrite stack
        # metadata while restoring an unavailable script.
        with zipfile.ZipFile(self._input) as archive:
            stacks = [name for name in archive.namelist() if name.startswith("Cura/")
                      and name.endswith(".global.cfg")]
            if len(stacks) != 1:
                raise RuntimeError("Expected exactly one Cura machine stack in the project")
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(archive.read(stacks[0]).decode("utf-8"))
            self._archived_postprocessing = config.get("metadata", "post_processing_scripts", fallback="")

        self._reader = self._app.getWorkspaceFileHandler().getReaderForFile(str(self._input))
        for attr in ("preRead", "read", "_dialog", "_loadAndAddNodesToScene",
                     "_deferred_node_file_name"):
            if not hasattr(self._reader, attr):
                raise RuntimeError("Unsupported 3MF workspace reader API: " + attr)

        # show_dialog=False returns too early in Cura 5.12.0: it does not finish
        # preparing MachineInfo or the conflict strategies. Run full preRead,
        # replacing only the GUI interaction with deterministic import choices.
        dialog = self._reader._dialog
        dialog.show = lambda: None
        dialog.waitForClose = lambda: None
        dialog.getResult = lambda: {
            "machine": "new", "quality_changes": "new",
            "definition_changes": "new", "material": "new"
        }
        self._phase = "reading_project"
        pre_result = self._reader.preRead(str(self._input))
        if pre_result != WorkspaceReader.PreReadResult.accepted:
            raise RuntimeError("Cura rejected the project or its printer definition is unavailable")
        if self._reader.getIsProjectUcp():
            raise RuntimeError("Universal Cura Projects are not supported; save a normal Cura project")
        missing = list(dialog.missingPackages)
        if missing:
            names = [str(item.get("id", item)) for item in missing]
            raise RuntimeError("Project requires unavailable Cura packages: " + ", ".join(names))

        original_load_nodes = self._reader._loadAndAddNodesToScene

        def load_nodes(file_name, is_ucp):
            try:
                original_load_nodes(file_name, is_ucp)
                self._scene_loaded = True
                self._validation_seen = False
                self._app.getMachineErrorChecker().startErrorCheck()
            except Exception as exc:
                self._fail("Project mesh loading failed: " + str(exc))

        self._reader._loadAndAddNodesToScene = load_nodes
        self._app.resetWorkspace()
        result = self._reader.read(str(self._input))
        if not isinstance(result, tuple) or len(result) != 2:
            raise RuntimeError("Cura did not return a valid project result")
        nodes, metadata = result
        if nodes is None or not self._reader.workspaceName():
            raise RuntimeError("Cura failed to import the project")
        # 5.12.0 returns an empty node list, then loads nodes after machine and
        # materials activation. Handle the reader contract and wait for that
        # deferred callback instead of relying on workspaceLoaded alone.
        scene = self._app.getController().getScene()
        for node in nodes:
            AddSceneNodeOperation(node, scene.getRoot()).push()
        self._app.getWorkspaceMetadataStorage().setAllData(metadata)
        self._app.workspaceLoaded.emit(self._reader.workspaceName())
        self._phase = "waiting_for_project"

    def _saved_printers(self):
        """Only user machine stacks; printer definitions alone are not choices."""
        stacks = self._app.getContainerRegistry().findContainerStacks(type="machine")
        return sorted(stacks, key=lambda stack: (stack.getName(), stack.getId()))

    def _disable_telemetry(self):
        # Supported Cura controls, also set by the launcher before process start.
        # Keep them disabled after importing a project or switching a machine.
        preferences = self._app.getPreferences()
        preferences.setValue("info/send_engine_crash", False)
        preferences.setValue("info/send_slice_info", False)
        os.environ["USE_SENTRY"] = "0"


    @staticmethod
    def _load_insert_gcode(path):
        path = path.resolve(strict=True)
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise RuntimeError("Insert-before-end G-code must be 1 MiB or smaller")
        if not raw.strip():
            raise RuntimeError("Insert-before-end G-code is empty")
        if b"\x00" in raw:
            raise RuntimeError("Insert-before-end G-code contains a NUL byte")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Insert-before-end G-code must be UTF-8 or ASCII") from exc
        text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
        encoded = text.encode("utf-8")
        return {
            "path": str(path),
            "name": path.name,
            "text": text,
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }

    @staticmethod
    def _printer_info(stack):
        return {
            "id": stack.getId(), "name": stack.getName(),
            "definition": stack.definition.getId(),
            "extruder_count": int(stack.getProperty("machine_extruder_count", "value")),
        }

    def _select_saved_target(self, selector):
        """Resolve before import adds a similarly named printer to the registry."""
        from cura.Settings.ExtruderManager import ExtruderManager

        if not isinstance(selector, str) or not selector.strip():
            raise RuntimeError("Printer must be an exact saved printer name or ID")
        printers = self._saved_printers()
        matches = [stack for stack in printers if stack.getId() == selector]
        if not matches:
            matches = [stack for stack in printers if stack.getName() == selector]
        if not matches:
            raise RuntimeError("Saved printer not found: " + selector + ". Use --list-printers first.")
        if len(matches) != 1:
            ids = ", ".join(stack.getId() for stack in matches)
            raise RuntimeError("Printer name is ambiguous: " + selector + ". Select an ID: " + ids)
        self._target = matches[0]
        # Load already-saved extruder stacks. Do not create default profiles.
        registry = self._app.getContainerRegistry()
        count = self._printer_info(self._target)["extruder_count"]
        saved_extruders = registry.findContainerStacks(type="extruder_train", machine=self._target.getId())
        by_position = {}
        expected_definitions = self._target.getMetaDataEntry("machine_extruder_trains", {}) or {}
        for extruder in saved_extruders:
            position = int(extruder.getMetaDataEntry("position"))
            if 0 <= position < count:
                if position in by_position:
                    raise RuntimeError("Saved target has duplicate extruder positions")
                by_position[position] = extruder
                expected = expected_definitions.get(str(position))
                if expected is not None and extruder.definition.getId() != expected:
                    raise RuntimeError("Saved target extruder definition needs migration; save it in Cura first")
        if count < 1 or set(by_position) != set(range(count)):
            raise RuntimeError("Saved target printer has incomplete extruders; save it in Cura first")
        ExtruderManager.getInstance().addMachineExtruders(self._target)
        positions = {int(stack.getMetaDataEntry("position")) for stack in self._target.extruderList}
        if count < 1 or positions != set(range(count)) or len(self._target.extruderList) != count:
            raise RuntimeError("Saved target printer has incomplete extruders; save it in Cura first")
        if not self._target.isValid():
            raise RuntimeError("Saved target printer has an invalid configuration")
        self._target_snapshot = self._machine_snapshot(self._target)
        self._target_postprocessing = self._target.getMetaDataEntry("post_processing_scripts", "") or ""

    @staticmethod
    def _explicit_values(container):
        return {key: str(container.getProperty(key, "value"))
                for key in sorted(container.getAllKeys())}

    def _machine_snapshot(self, stack):
        """Compare saved component IDs and explicit values, not context formulas."""
        result = {"machine_id": stack.getId(), "stacks": {},
                  "post_processing_scripts": stack.getMetaDataEntry("post_processing_scripts", "") or "",
                  "enabled_extruder_count": sum(bool(item.isEnabled) for item in stack.extruderList)}
        for label, current in [("global", stack)] + [
                ("extruder_" + str(item.getMetaDataEntry("position")), item)
                for item in stack.extruderList]:
            components = []
            for container in current.getContainers():
                item = {"id": container.getId()}
                # Definition defaults are installed resources, not saved user
                # overrides. Their IDs are sufficient and their formulas must
                # not be evaluated against the currently active other machine.
                if container.getMetaDataEntry("type") not in ("machine", "extruder"):
                    item["values"] = self._explicit_values(container)
                    if current is stack and container is stack.definitionChanges:
                        # MachineManager.updateNumberExtrudersEnabled always
                        # regenerates this one bookkeeping field on activation.
                        # The same computed count is compared above; the active
                        # runtime value is additionally checked below.
                        item["values"].pop("extruders_enabled_count", None)
                components.append(item)
            result["stacks"][label] = {
                "id": current.getId(), "components": components,
                "enabled": str(current.getMetaDataEntry("enabled", "True")),
            }
        return result

    def _assert_target_unchanged(self):
        actual = self._machine_snapshot(self._target)
        if self._target_applied:
            runtime_count = self._target.getProperty("extruders_enabled_count", "value")
            if runtime_count != actual["enabled_extruder_count"]:
                raise RuntimeError("Target's runtime enabled-extruder count is inconsistent")
        if actual != self._target_snapshot:
            differences = []

            def compare(before, after, path):
                if isinstance(before, dict) and isinstance(after, dict):
                    for key in sorted(set(before) | set(after)):
                        if key not in before or key not in after:
                            differences.append(path + "." + key + ": added/removed")
                        else:
                            compare(before[key], after[key], path + "." + key)
                elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
                    for index, (old, new) in enumerate(zip(before, after)):
                        compare(old, new, path + "[" + str(index) + "]")
                elif before != after:
                    differences.append(path + ": " + repr(before)[:100] + " -> " + repr(after)[:100])

            compare(self._target_snapshot, actual, "target")
            raise RuntimeError("Saved target printer settings changed or fell back during activation; "
                               "open that printer in Cura, check its profiles, and save it again. "
                               + "; ".join(differences[:8]))

    def _switch_target(self):
        from cura.Machines.ContainerTree import ContainerTree

        self._phase = "switching_printer"
        self._backend.disableTimer()
        checker = self._app.getMachineErrorChecker()
        # _poll_safely waits for the source pass to finish before entering here.
        # Its cached error-key subset must not narrow the new target pass.
        checker._keys_to_check.clear()
        self._validation_seen = False
        source = self._app.getGlobalContainerStack()
        if source is None:
            raise RuntimeError("Project did not activate a source printer")
        self._source_printer = self._printer_info(source)
        source_by_id = {item.getId(): str(item.getMetaDataEntry("position"))
                        for item in source.extruderList}
        target_by_position = {str(item.getMetaDataEntry("position")): item
                              for item in self._target.extruderList}
        if (len(source_by_id) != len(target_by_position)
                or set(source_by_id.values()) != set(target_by_position)):
            raise RuntimeError("Source and target printer extruder counts/positions must match: "
                               + str(len(source_by_id)) + " -> " + str(len(target_by_position)))
        self._assert_target_unchanged()
        root = self._app.getController().getScene().getRoot()
        plan = []
        for node in DepthFirstIterator(root):
            per_object = node.callDecoration("getStack")
            if per_object is None or not (node.callDecoration("isSliceable") or node.callDecoration("isGroup")):
                continue
            old_extruder = node.callDecoration("getActiveExtruder")
            if old_extruder not in source_by_id:
                raise RuntimeError("Model has an unknown source extruder: " + node.getName())
            position = source_by_id[old_extruder]
            target_extruder = target_by_position[position]
            if not target_extruder.isEnabled:
                raise RuntimeError("Target extruder " + position + " is disabled for model " + node.getName())
            overrides = self._explicit_values(per_object.getTop())
            raw_overrides = {key: per_object.getTop().getProperty(key, "value") for key in overrides}
            unknown = sorted(set(overrides) - set(target_extruder.getAllKeys()))
            if unknown:
                raise RuntimeError("Target printer does not support model settings: " + ", ".join(unknown))
            plan.append((node, target_extruder, position, overrides, raw_overrides,
                         node.getWorldTransformation().getData().tolist()))
        if not plan:
            raise RuntimeError("Project contains no models to transfer to the target printer")

        self._app.getMachineManager().setActiveMachine(self._target.getId())
        active = self._app.getGlobalContainerStack()
        if active is None or active.getId() != self._target.getId():
            raise RuntimeError("Cura could not activate the selected saved printer")
        definition = ContainerTree.getInstance().machines[self._target.definition.getId()].quality_definition
        for node, extruder, position, overrides, raw_overrides, transform in plan:
            node.callDecoration("setActiveExtruder", extruder.getId())
            per_object = node.callDecoration("getStack")
            top = per_object.getTop()
            top.setDefinition(definition)
            # setDefinition affects only new SettingInstances. Rebuild the
            # explicit instances so their validators also use the target's
            # definitions, while preserving every original override value.
            top.clear()
            for key, value in raw_overrides.items():
                top.setProperty(key, "value", value)
            if per_object.getNextStack() is None or per_object.getNextStack().getId() != extruder.getId():
                raise RuntimeError("Could not rebind model settings to target extruder: " + node.getName())
            if self._explicit_values(per_object.getTop()) != overrides:
                raise RuntimeError("Per-model overrides changed while switching printer: " + node.getName())
            if node.getWorldTransformation().getData().tolist() != transform:
                raise RuntimeError("Model placement changed while switching printer: " + node.getName())
            self._object_assignments.append({"name": node.getName(), "extruder_position": position,
                                             "target_extruder_id": extruder.getId()})
        self._target_applied = True
        self._validation_seen = False
        checker._keys_to_check.clear()
        checker.startErrorCheck()
        self._phase = "waiting_for_target"

    def _poll_safely(self):
        if self._done:
            return
        try:
            if time.monotonic() - self._started_at > self._timeout:
                raise TimeoutError("Timed out during " + self._phase)
            if self._phase in ("waiting_for_project", "waiting_for_target"):
                if not self._scene_loaded or self._reader._deferred_node_file_name is not None:
                    return
                if self._app.getMachineManager()._instance_container_timer.isActive():
                    return
                checker = self._app.getMachineErrorChecker()
                # Cura briefly reports neither pending nor running between
                # scheduling _checkStack and actually calling it. An observed
                # completion signal is required, not only those two flags.
                if (not self._validation_seen or checker.needToWaitForResult
                        or checker._error_check_timer.isActive()):
                    return
                if self._target is not None and not self._target_applied:
                    # Source process settings are being replaced, so a genuine
                    # source error may be repaired by the selected target. Only
                    # its validation completion, not its result, gates switching.
                    self._switch_target()
                    return
                if checker.hasError:
                    raise RuntimeError("Cura reports invalid machine or slicing settings")
                self._begin_slice()
            elif self._phase == "slicing":
                # backendDone is emitted before the engine backend replaces
                # {print_time}, {jobname}, etc. Wait until its handler returns.
                if self._backend_done and not self._backend._slicing:
                    if self._backend._build_plates_to_be_sliced:
                        return
                    self._export()
        except Exception as exc:
            self._fail(str(exc))

    def _scene_nodes(self):
        root = self._app.getController().getScene().getRoot()
        return [node for node in DepthFirstIterator(root)
                if node.getMeshData() is not None and node.callDecoration("isSliceable")]

    def _on_validation_wait_changed(self):
        if self._app.getMachineErrorChecker().needToWaitForResult:
            self._validation_seen = False

    def _on_validation_finished(self):
        if self._scene_loaded:
            self._validation_seen = True

    def _validate_imported_profiles(self, stack):
        """Catch Cura's normal fallback to another quality, variant or material."""
        from cura.Machines.ContainerTree import ContainerTree

        info = self._reader._machine_info
        if stack.definition.getId() != info.definition_id:
            raise RuntimeError("Imported machine definition did not match the project")
        quality_name = self._reader._quality_changes_to_apply
        quality_type = self._reader._quality_type_to_apply
        if quality_name is not None:
            if stack.qualityChanges.getName() != quality_name:
                raise RuntimeError("Project's custom quality profile could not be restored")
        elif quality_type is not None and stack.quality.getMetaDataEntry("quality_type") != quality_type:
            raise RuntimeError("Project's quality type could not be restored: " + str(quality_type))

        machine = ContainerTree.getInstance().machines[info.definition_id]
        if info.variant_info is not None:
            expected_variant = info.variant_info.parser["general"]["name"]
            if stack.variant.getName() != expected_variant:
                raise RuntimeError("Project's global printer variant is unavailable")
        for extruder in stack.extruderList:
            position = str(extruder.getMetaDataEntry("position"))
            expected = info.extruder_info_dict.get(position)
            if expected is None:
                raise RuntimeError("Project has no settings for extruder " + position)
            if expected.variant_info is not None:
                name = expected.variant_info.parser["general"]["name"]
                if extruder.variant.getName() != name:
                    raise RuntimeError("Project's nozzle variant is unavailable: " + name)
            root_id = expected.root_material_id
            if root_id is not None:
                root_id = self._reader._old_new_materials.get(root_id, root_id)
                available = machine.variants[extruder.variant.getName()].materials
                wanted = available.get(root_id)
                if wanted is None or wanted.container.getId() != extruder.material.getId():
                    raise RuntimeError("Project's material could not be restored: " + root_id)

    def _begin_slice(self):
        nodes = self._scene_nodes()
        if not nodes:
            raise RuntimeError("Project contains no sliceable models")
        plates = {node.callDecoration("getBuildPlateNumber") for node in nodes}
        if len(plates) != 1 or any(not isinstance(n, int) or n < 0 for n in plates):
            raise RuntimeError("Only a single populated build plate is supported; found " + repr(plates))
        self._plate = next(iter(plates))
        self._app.getMultiBuildPlateModel().setActiveBuildPlate(self._plate)
        stack = self._app.getGlobalContainerStack()
        if stack is None:
            raise RuntimeError("Project did not activate a printer")
        if self._target_applied:
            if stack.getId() != self._target.getId():
                raise RuntimeError("Active printer changed before slicing")
            self._assert_target_unchanged()
        else:
            self._validate_imported_profiles(stack)
        self._validate_build_volume(nodes)
        self._prepare_postprocessing(stack)
        self._summary = self._capture_summary(stack, nodes)
        self._phase = "slicing"
        self._backend_done = False
        self._backend.disableTimer()
        self._disable_telemetry()
        self._backend.forceSlice()

    def _validate_build_volume(self, nodes):
        """Restore Cura's geometry boundary checks without creating QML meshes.

        In 5.12.0 BuildVolume.rebuild() returns before creating _volume_aabb
        when no QML engine has been created. The formulas below are the native
        rebuild formulas; disallowed areas and node checks stay in Cura itself.
        """
        volume = self._app.getBuildVolume()
        required = ("_updateScaleFactor", "_updateRootPrintableObjectCount",
                    "_onStackChangeTimerFinished", "_stack_change_timer",
                    "_volume_aabb", "_raft_thickness", "_extra_z_clearance")
        for attr in required:
            if not hasattr(volume, attr):
                raise RuntimeError("Unsupported build-volume API: " + attr)
        volume._updateScaleFactor()
        volume._updateRootPrintableObjectCount()
        volume._stack_change_timer.stop()
        volume._onStackChangeTimerFinished()
        width, height, depth = volume.getWidth(), volume.getHeight(), volume.getDepth()
        if not all(math.isfinite(float(n)) and float(n) > 0 for n in (width, height, depth)):
            raise RuntimeError("Printer has invalid build-volume dimensions")
        volume._volume_aabb = AxisAlignedBox(
            minimum=Vector(-width / 2, -1.0, -depth / 2),
            maximum=Vector(width / 2, height - volume._raft_thickness - volume._extra_z_clearance,
                           depth / 2),
        )
        volume.updateNodeBoundaryCheck()
        if volume.hasErrors():
            raise RuntimeError("Cura reports a build-volume or prime-tower collision")
        outside = []
        for node in nodes:
            per_object = node.callDecoration("getStack")
            modifier = per_object is not None and any(
                bool(per_object.getProperty(key, "value"))
                for key in ("anti_overhang_mesh", "support_mesh", "cutting_mesh", "infill_mesh")
            )
            if not modifier and node.isOutsideBuildArea():
                outside.append(node.getName())
        if outside:
            raise RuntimeError("Models outside the printable build volume: " + ", ".join(outside))

    def _prepare_postprocessing(self, stack):
        encoded = self._target_postprocessing if self._target_applied else self._archived_postprocessing
        requested = []
        for chunk in encoded.split("\n"):
            if not chunk:
                continue
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read_string(chunk.replace(r"\\\n", "\n").replace(r"\\\\", "\\\\"))
            requested.extend(parser.sections())
        registry = self._app.getPluginRegistry()
        plugin = (registry.getPluginObject("PostProcessingPlugin")
                  if "PostProcessingPlugin" in registry.getActivePlugins() else None)
        if requested and plugin is None:
            raise RuntimeError("Project requests post-processing, but PostProcessingPlugin is unavailable")
        restored = list(plugin.scriptList) if plugin is not None else []
        if requested != restored:
            raise RuntimeError("Post-processing scripts could not be restored: " + repr(requested))
        self._post_names = requested
        if plugin is not None:
            for script in plugin._script_list:
                original = script.execute
                name = script.getSettingData()["key"]

                def checked_execute(data, original=original, name=name):
                    try:
                        result = original(data)
                        if not isinstance(result, list) or not all(isinstance(x, str) for x in result):
                            raise RuntimeError("Script did not return a list of G-code strings")
                        return result
                    except Exception as exc:
                        self._post_errors.append(name + ": " + str(exc))
                        raise

                self._post_originals.append((script, original))
                script.execute = checked_execute

    @staticmethod
    def _json_hash(value):
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _capture_summary(self, stack, nodes):
        all_settings = {}
        for label, container in [("global", stack)] + [
                ("extruder_" + str(s.getMetaDataEntry("position")), s)
                for s in stack.extruderList]:
            all_settings[label] = {
                key: str(container.getProperty(key, "value"))
                for key in sorted(container.getAllKeys())
            }
        placement = []
        for node in nodes:
            mesh = node.getMeshData()
            geometry = hashlib.sha256(mesh.getVertices().tobytes())
            indices = mesh.getIndices()
            if indices is not None:
                geometry.update(indices.tobytes())
            item = {
                "name": node.getName(),
                "world_transform": node.getWorldTransformation().getData().tolist(),
                "build_plate": node.callDecoration("getBuildPlateNumber"),
                "geometry_sha256": geometry.hexdigest(),
            }
            object_stack = node.callDecoration("getStack")
            if object_stack is not None:
                top = object_stack.getTop()
                item["object_overrides"] = {
                    key: str(top.getProperty(key, "value"))
                    for key in sorted(top.getAllKeys())
                }
                item["object_settings_sha256"] = self._json_hash({
                    key: str(object_stack.getProperty(key, "value"))
                    for key in sorted(object_stack.getAllKeys())
                })
            placement.append(item)
        keys = ("layer_height", "layer_height_0", "infill_sparse_density", "speed_print", "machine_nozzle_size",
                "material_print_temperature", "material_bed_temperature", "machine_width",
                "machine_depth", "machine_height", "machine_gcode_flavor")
        return {
            "cura_version": self._app.getVersion(),
            "machine_definition": stack.definition.getId(),
            "machine_name": stack.getName(),
            "build_plate": self._plate,
            "model_count": len(nodes),
            "extruder_count": len(stack.extruderList),
            "settings_sha256": self._json_hash(all_settings),
            "placement_sha256": self._json_hash(placement),
            "settings": {label: {key: values.get(key) for key in keys}
                         for label, values in all_settings.items()},
            "models": placement,
            "postprocessing_scripts": list(self._post_names),
            "printer_policy": "target_saved_settings_with_project_object_overrides" if self._target_applied else "project_settings",
            "selected_printer": self._printer_info(stack) if self._target_applied else None,
            "source_printer": self._source_printer,
            "extruder_assignments": self._object_assignments,
            "target_saved_settings_sha256": self._json_hash(self._target_snapshot) if self._target_applied else None,
            "insert_before_end": ({"name": self._insert_before_end["name"],
                                   "bytes": self._insert_before_end["bytes"],
                                   "sha256": self._insert_before_end["sha256"]}
                                  if self._insert_before_end is not None else None),
        }

    def _on_backend_done(self, *args):
        if self._phase == "slicing":
            self._backend_done = True

    def _on_backend_state(self, state):
        if not self._done and self._phase == "slicing" and state in (BackendState.Error, BackendState.Disabled):
            self._fail("CuraEngine reported " + str(state))

    def _on_backend_error(self, *args):
        if not self._done and self._phase == "slicing":
            self._fail("CuraEngine could not slice this project; see the Cura log")


    def _inject_before_end(self, gcode):
        if self._insert_before_end is None:
            return
        if not isinstance(gcode, list) or len(gcode) < 2 or not all(isinstance(chunk, str) for chunk in gcode):
            raise RuntimeError("Unexpected Cura G-code section structure; cannot insert before end G-code safely")
        end_index = None
        for index in range(len(gcode) - 1, -1, -1):
            if gcode[index].strip():
                end_index = index
                break
        if end_index is None or end_index == 0:
            raise RuntimeError("Cura end G-code section could not be identified")
        name = self._insert_before_end["name"]
        payload = (
            "\n;-- cura_cmdline_helper insert-before-end: BEGIN " + name + " --\n"
            + self._insert_before_end["text"]
            + ";-- cura_cmdline_helper insert-before-end: END " + name + " --\n"
        )
        gcode[end_index] = payload + gcode[end_index]

    def _export(self):
        self._phase = "writing_gcode"
        scene = self._app.getController().getScene()
        gcode = getattr(scene, "gcode_dict", {}).get(self._plate)
        if not gcode or not any(line.strip() for line in gcode):
            raise RuntimeError("Slicing finished without G-code")
        self._inject_before_end(gcode)

        # Standard Cura post-processing runs on writeStarted, not in GCodeWriter.
        # Emit the same manager hook before invoking the normal text writer.
        manager = self._app.getOutputDeviceManager()
        self._disable_telemetry()
        device = manager.getOutputDevice("local_file")
        if device is None:
            device = OutputDevice("headless_reslice")
        manager.writeStarted.emit(device)
        if self._post_errors:
            raise RuntimeError("Post-processing failed: " + "; ".join(self._post_errors))
        writer = self._app.getMeshFileHandler().getWriter("GCodeWriter")
        if writer is None:
            raise RuntimeError("Cura's GCodeWriter is unavailable")
        # The launcher reserves this unique temporary output with mkstemp.
        # Publication/overwrite policy is enforced by the launcher afterward.
        with self._output.open("w", encoding="utf-8", newline="\n") as stream:
            self._output_created = True
            if not writer.write(stream, [], mode=MeshWriter.OutputMode.TextMode):
                raise RuntimeError("GCodeWriter failed: " + str(writer.getInformation()))
            stream.flush()
            os.fsync(stream.fileno())
        if self._output.stat().st_size == 0:
            raise RuntimeError("GCodeWriter produced an empty output")
        digest = hashlib.sha256()
        with self._output.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        self._finish({
            "status": "success", "success": True, "ok": True,
            "input_path": str(self._input), "output_path": str(self._output),
            "output_bytes": self._output.stat().st_size,
            "gcode_sha256": digest.hexdigest(), "summary": self._summary,
        }, 0)

    def _uncaught_exception(self, exc_type, exc, tb):
        self._fail("Unhandled Cura exception: " + str(exc),
                   "".join(traceback.format_exception(exc_type, exc, tb)))

    def _fail(self, message, trace=None):
        if self._done:
            return
        if self._output_created:
            try:
                self._output.unlink(missing_ok=True)
            except OSError:
                pass
        Logger.log("e", "Headless reslice: %s", message)
        result = {"status": "error", "success": False, "ok": False,
                  "error": message, "phase": self._phase,
                  "traceback": trace or traceback.format_exc()}
        if self._backend is not None:
            try:
                result["engine_log_tail"] = [
                    line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
                    for line in self._backend.getLog()[-50:]
                ]
            except Exception:
                pass
        self._finish(result, 1)

    def _finish(self, result, exit_code):
        if self._done:
            return
        self._done = True
        self._timer.stop()
        result["job_id"] = self._manifest.get("job_id")
        result["elapsed_seconds"] = round(time.monotonic() - self._started_at, 3)
        try:
            if self._result_path is not None:
                temporary = self._result_path.with_name(self._result_path.name + ".tmp")
                with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self._result_path)
        except Exception:
            Logger.logException("e", "Could not write the headless reslice result")
            exit_code = 1
        QTimer.singleShot(0, lambda: self._shutdown(exit_code))

    def _shutdown(self, exit_code):
        try:
            if self._backend is not None:
                self._backend.close()
        finally:
            QCoreApplication.exit(exit_code)
