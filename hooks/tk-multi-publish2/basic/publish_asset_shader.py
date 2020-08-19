# Copyright (c) 2017 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import os
import re
import pprint
import maya.cmds as cmds
import sgtk

from tank_vendor import six

HookBaseClass = sgtk.get_hook_baseclass()


class MayaAssetShaderExport(HookBaseClass):
    """
    Plugin to export asset shaders.
    """

    @property
    def icon(self):
        """
        Path to an png icon on disk
        """

        # look for icon one level up from this hook's folder in "icons" folder
        return os.path.join(self.disk_location, os.pardir, "icons", "shader.png")

    @property
    def name(self):
        """
        One line display name describing the plugin
        """
        return "Shader export"

    @property
    def description(self):
        """
        Verbose, multi-line description of what the plugin does. This can
        contain simple html for formatting.
        """
        return """
        Export maya shaders by asset interaction.<br><br>

        """

    @property
    def item_filters(self):
        """
        List of item types that this plugin is interested in.

        Only items matching entries in this list will be presented to the
        accept() method. Strings can contain glob patters such as *, for example
        ["maya.*", "file.maya"]
        """
        return ["maya.asset.shader"]

    @property
    def settings(self):
        """
        Dictionary defining the settings that this plugin expects to receive
        through the settings parameter in the accept, validate, publish and
        finalize methods.

        A dictionary on the following form::

            {
                "Settings Name": {
                    "type": "settings_type",
                    "default": "default_value",
                    "description": "One line description of the setting"
            }

        The type string should be one of the data types that toolkit accepts as
        part of its environment configuration.
        """
        # inherit the settings from the base publish plugin
        base_settings = super(MayaAssetShaderExport, self).settings or {}

        # settings specific to this class
        maya_publish_settings = {
            "Publish Template": {
                "type": "template",
                "default": None,
                "description": "Template path for published work files. Should"
                "correspond to a template defined in "
                "templates.yml.",
            }
        }

        # update the base settings
        base_settings.update(maya_publish_settings)

        return base_settings

    def accept(self, settings, item):
        """
        Method called by the publisher to determine if an item is of any
        interest to this plugin. Only items matching the filters defined via the
        item_filters property will be presented to this method.

        A publish task will be generated for each item accepted here. Returns a
        dictionary with the following booleans:

            - accepted: Indicates if the plugin is interested in this value at
                all. Required.
            - enabled: If True, the plugin will be enabled in the UI, otherwise
                it will be disabled. Optional, True by default.
            - visible: If True, the plugin will be visible in the UI, otherwise
                it will be hidden. Optional, True by default.
            - checked: If True, the plugin will be checked in the UI, otherwise
                it will be unchecked. Optional, True by default.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process

        :returns: dictionary with boolean keys accepted, required and enabled
        """

        if item.properties.get("shader_iter") is None:
            return {"accepted": False}
        elif len(item.properties.get("shader_iter").nodes) == 0:
            return {"accepted": False}

        accepted = True
        publisher = self.parent
        self.logger.info(settings)
        # template_name = settings["Publish Template"].value

        # ensure a work file template is available on the parent item
        work_template = item.parent.properties.get("work_template")
        if not work_template:
            self.logger.debug(
                "A work template is required for the session item in order to "
                "publish session geometry. Not accepting session geom item."
            )
            accepted = False

        # ensure the publish template is defined and valid and that we also have
        publish_template = publisher.get_template_by_name("maya_asset_shader")
        self.logger.info(publish_template)
        if not publish_template:
            self.logger.debug(
                "The valid publish template could not be determined for the "
                "session geometry item. Not accepting the item."
            )
            accepted = False

        # we've validated the publish template. add it to the item properties
        # for use in subsequent methods
        item.properties["publish_template"] = publish_template

        # because a publish template is configured, disable context change. This
        # is a temporary measure until the publisher handles context switching
        # natively.
        item.context_change_allowed = False

        return {"accepted": accepted, "checked": True}

    def validate(self, settings, item):
        """
        Validates the given item to check that it is ok to publish.

        Returns a boolean to indicate validity.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process

        :returns: True if item is valid, False otherwise.
        """

        shader_iter = item.properties.get("shader_iter")
        shader_iter.fetch()

        if not len(shader_iter):
            return False

        # get the configured work file template
        work_template = item.parent.properties.get("work_template")
        publish_template = item.properties.get("publish_template")

        path = _session_path()

        # ---- ensure the session has been saved

        if not path:
            # the session still requires saving. provide a save button.
            # validation fails.
            error_msg = "The Maya session has not been saved."
            self.logger.error(error_msg, extra=_get_save_as_action())
            raise Exception(error_msg)

        # get the normalized path
        path = sgtk.util.ShotgunPath.normalize(path)

        # get the current scene path and extract fields from it using the work
        # template:
        work_fields = work_template.get_fields(path)

        # ensure the fields work for the publish template
        missing_keys = publish_template.missing_keys(work_fields)
        if missing_keys:
            error_msg = (
                "Work file '%s' missing keys required for the "
                "publish template: %s" % (path, missing_keys)
            )
            self.logger.error(error_msg)
            raise Exception(error_msg)

        # create the publish path by applying the fields. store it in the item's
        # properties. This is the path we'll create and then publish in the base
        # publish plugin. Also set the publish_path to be explicit.
        item.properties["path"] = publish_template.apply_fields(work_fields)
        item.properties["publish_path"] = item.properties["path"]

        # use the work file's version number when publishing
        if "version" in work_fields:
            item.properties["publish_version"] = work_fields["version"]

        # run the base class validation
        return super(MayaAssetShaderExport, self).validate(settings, item)

    def publish(self, settings, item):
        """
        Executes the publish logic for the given item and settings.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """

        publisher = self.parent

        # get the path in a normalized state. no trailing separator, separators
        # are appropriate for current os, no double separators, etc.
        path = sgtk.util.ShotgunPath.normalize(_session_path())

        # ensure the session is saved in its current state
        _save_session(path)

        # get the path to create and publish
        publish_path = item.properties["path"]
        publish_dir = os.path.dirname(publish_path)

        # make sure this folder already exists
        if not os.path.isdir(publish_dir):
            try:
                os.makedirs(publish_dir)
            except Exception as e:
                self.logger.error(
                    "Unable to make the directory {}, because: {}".format(
                        publish_dir, e
                    )
                )

        engine = sgtk.platform.current_engine()
        sg = engine.shotgun
        context = engine.context

        # Consulado framework init
        tk_consuladoutils = self.load_framework(
            "tk-framework-consuladoutils_v0.x.x"
        )
        consulado_globals = self.tk_consuladoutils.import_module("shotgun_globals")
        maya_utils = self.tk_consuladoutils.import_module("maya_utils")
        consulado_model = self.tk_consuladoutils.import_module("shotgun_model")

        sg_node_name = self.consulado_globals.get_custom_entity_by_alias("node")
        sg_node_type_name = self.consulado_globals.get_custom_entity_by_alias(
            "node_type"
        )
        node_fields = [
            "project",
            "id",
            "code",
            "sg_link",
            "sg_node_type",
            "sg_downstream_node",
            "sg_upstream_node",
            "published_file",
        ]
        Nodes = consulado_model.EntityIter(sg_node_name, node_fields, context, sg)

        shader_iter = item.properties.get("shader_iter")
        for shader in shader_iter:
            version_number = self._get_next_shader_version_number(
                publish_dir, shader.shading_engine.nodeName()
            )
            shader_path = os.path.join(
                publish_dir,
                "{}.v{:03d}".format(shader.shading_engine.nodeName(), version_number,),
            )
            shader.export(shader_path)

            publish_data = {
                "tk": publisher.sgtk,
                "context": item.context,
                "comment": item.description,
                "path": shader_path,
                "name": shader.shading_engine.nodeName(),
                "created_by": item.get_property("publish_user", default_value=None),
                "version_number": version_number,
                "thumbnail_path": item.get_thumbnail_as_path(),
                "published_file_type": "Maya Shader",
                "dependency_paths": [],
            }
            # log the publish data for debugging
            self.logger.debug(
                "Populated Publish data...",
                extra={
                    "action_show_more_info": {
                        "label": "Publish Data",
                        "tooltip": "Show the complete Publish data dictionary",
                        "text": "<pre>%s</pre>" % (pprint.pformat(publish_data),),
                    }
                },
            )
            publish_result = sgtk.util.register_publish(**publish_data)

            node = Nodes.add_new_entity()
            node.code = shader.shading_engine.nodeName()
            node.sg_link = publish_result.get("entity")
            node.sg_node_type = {"type": sg_node_type_name, "id": 4}
            node.published_file = {"type":"PublishedFile", "id": publish_result.get("id")}
            node.sg_upstream_node = [
                {"type": sg_node_name, "id": i} 
                for i.getTransform().cNodeId.get() in shader.nodes
            ]
            node.load()
            self.logger.debug(
                "Found the Shotguns entity node: %s" % node.shotgun_entity_data
            )
            if node.id is None:
                node.create()
            else:
                node.update()

            # TODO: Vicular o Node shader no downstream do Node mesh
            self.logger.debug(
                "publish result: {}".format(pprint.pformat(publish_result))
            )

            self.logger.info(
                "Shader {} exported to {} successfully".format(
                    shader.shading_engine.nodeName(), shader_path
                )
            )

        return super(MayaAssetShaderExport, self).publish(settings, item)

    def finalize(self, settings, item):
        """
        Execute the finalization pass. This pass executes once
        all the publish tasks have completed, and can for example
        be used to version up files.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """

        return super(MayaAssetShaderExport, self).finalize(settings, item)

    def _get_next_shader_version_number(self, path, file_name):
        files = [f for f in os.listdir(path) if file_name in f]
        version = 0
        files.sort()

        for match in re.findall("(\w+.v(\d{3}).\w+)", files[-1]):
            f, v = match
            v = int(v)
            if version < v:
                version = v

        return version + 1

    def _get_version_number(self, path, item):
        """
        Try to extract and return a version number for the supplied path.

        :param path: The path to the current session

        :return: The version number as an `int` if it can be determined, else
            None.

        NOTE: This method will use the work template provided by the
        session collector, if configured, to determine the version number. If
        not configured, the version number will be extracted using the zero
        config path_info hook.
        """

        publisher = self.parent
        version_number = None

        work_template = item.properties.get("work_template")
        if work_template:
            if work_template.validate(path):
                self.logger.debug("Using work template to determine version number.")
                work_fields = work_template.get_fields(path)
                if "version" in work_fields:
                    version_number = work_fields.get("version")
            else:
                self.logger.debug("Work template did not match path")
        else:
            self.logger.debug("Work template unavailable for version extraction.")

        if version_number is None:
            self.logger.debug("Using path info hook to determine version number.")
            version_number = publisher.util.get_version_number(path)

        return version_number


def _session_path():
    """
    Return the path to the current session
    :return:
    """
    path = cmds.file(query=True, sn=True)

    if path is not None:
        path = six.ensure_str(path)

    return path


def _save_session(path):
    """
    Save the current session to the supplied path.
    """

    # Maya can choose the wrong file type so we should set it here
    # explicitly based on the extension
    maya_file_type = None
    if path.lower().endswith(".ma"):
        maya_file_type = "mayaAscii"
    elif path.lower().endswith(".mb"):
        maya_file_type = "mayaBinary"

    cmds.file(rename=path)

    # save the scene:
    if maya_file_type:
        cmds.file(save=True, force=True, type=maya_file_type)
    else:
        cmds.file(save=True, force=True)


# TODO: method duplicated in all the maya hooks
def _get_save_as_action():
    """

    Simple helper for returning a log action dict for saving the session
    """

    engine = sgtk.platform.current_engine()

    # default save callback
    callback = cmds.SaveScene

    # if workfiles2 is configured, use that for file save
    if "tk-multi-workfiles2" in engine.apps:
        app = engine.apps["tk-multi-workfiles2"]
        if hasattr(app, "show_file_save_dlg"):
            callback = app.show_file_save_dlg

    return {
        "action_button": {
            "label": "Save As...",
            "tooltip": "Save the current session",
            "callback": callback,
        }
    }


def _get_version_docs_action():
    """
    Simple helper for returning a log action to show version docs
    """
    return {
        "action_open_url": {
            "label": "Version Docs",
            "tooltip": "Show docs for version formats",
            "url": "https://support.shotgunsoftware.com/hc/en-us/articles/115000068574-User-Guide-WIP-#What%20happens%20when%20you%20publish",
        }
    }
