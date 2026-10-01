import json
import re
from typing import Any

from rest_framework import serializers
from rest_framework.request import Request, QueryDict

from dashboard.models import Organization

from .models import CustomFieldDefinition, CustomFieldValue
from .utils import _validate_field_value, get_definitions_for_module


class CustomFieldDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomFieldDefinition
        fields = [
            "id",
            "module",
            "field_label",
            "field_key",
            "field_type",
            "is_required",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "field_key"]

    @staticmethod
    def build_field_key(module, label):
        slug = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
        slug = re.sub(r"_+", "_", slug)
        key = f"{module}_{slug}" if module and slug else (module or "")
        return key[:100].strip("_")

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        module = attrs.get("module", getattr(instance, "module", None))
        label = attrs.get("field_label", getattr(instance, "field_label", None))
        attrs["field_key"] = (
            instance.field_key if instance else self.build_field_key(module, label)
        )
        return attrs

    def validate_field_key(self, value):
        # Ignore any client-supplied key; the value is always auto-generated.
        return value


class CustomFieldValueSerializer(serializers.ModelSerializer):
    field_key = serializers.CharField(source="definition.field_key", read_only=True)

    class Meta:
        model = CustomFieldValue
        fields = ["id", "entity_uuid", "field_key", "value_text", "created_at"]
        read_only_fields = ["id", "created_at"]


class CustomFieldsSerializerFieldMixin(metaclass=serializers.SerializerMetaclass):
    """
    Mixin to provide the custom_fields serializer field
    and related methods that should be used for saving custom fields
    properly.

    To use this mixin, the following needs to be done:

    - `custom_fields` entry needs to be added to the Meta.fields list.

    - The CUSTOM_FIELD_MODULE_NAME attribute needs to be set.
      Required for getting custom field definitions.

    - Add the set_custom_fields_internal_value method inside the to_internal_value method.

    - Add the create_custom_field_entries method inside the create method.
      Make sure custom fields are created using this method only.

    - Add the update_custom_field_entries method inside the update method.
      Make sure custom fields are updated using this method only.
    """

    custom_fields = serializers.JSONField(required=False, default=dict)

    def get_mutable_dict(
        self,
        data: QueryDict,
        empty_values: tuple = ("", None),
        disable_pop: bool = False,
    ) -> dict[str, Any]:
        """Helper method to convert provided QueryDict to mutable dict object.

        This also pops keys whose values have been set as one of the values
        that are provided in the empty_values parameter. This can be disabled
        using the disable_pop parameter.
        """

        _data = {}
        for key, value in data.lists():
            if disable_pop is False and all(_value in empty_values for _value in value):
                continue

            _data[key] = value[0] if len(value) == 1 else value

        return _data

    def set_custom_fields_internal_value(self, data: dict) -> dict:
        """Set proper custom_fields data in provided data payload.

        Use this inside the to_internal_value method.
        """

        custom_fields = data.get("custom_fields")
        if custom_fields in ["", None]:
            data.pop("custom_fields", None)
        elif isinstance(custom_fields, str):
            try:
                data["custom_fields"] = json.loads(custom_fields)
            except json.JSONDecodeError:
                raise serializers.ValidationError(
                    {"custom_fields": "Invalid JSON format"}
                )

        return data

    @property
    def custom_field_module_name(self):
        if hasattr(self, "__custom_field_module_name"):
            return self.__custom_field_module_name

        if not hasattr(self, "CUSTOM_FIELD_MODULE_NAME"):
            raise ValueError("Please set the CUSTOM_FIELD_MODULE_NAME attribute")

        custom_field_module_name: str = getattr(self, "CUSTOM_FIELD_MODULE_NAME")
        allowed_custom_field_module_names = [
            value for value, label in CustomFieldDefinition.MODULE_CHOICES
        ]
        if custom_field_module_name not in allowed_custom_field_module_names:
            raise ValueError(
                "Invalid CUSTOM_FIELD_MODULE_NAME value provided. Accepted values: "
                + ", ".join(allowed_custom_field_module_names)
            )

        self.__custom_field_module_name: str = custom_field_module_name
        return self.__custom_field_module_name

    def get_auth_user_organization(self, *args, **kwargs) -> Organization | None:
        request: Request = self.context.get("request", None)
        if not isinstance(request, Request):
            raise ValueError(
                "Please provide the proper `request` context to serializer"
            )

        organization: Organization | None = getattr(request.user, "organization", None)
        return organization

    def get_custom_field_definitions_for_module(
        self, organization: Organization | None, module: str
    ):
        if hasattr(self, f"_{module}_custom_field_definitions"):
            return getattr(self, f"_{module}_custom_field_definitions")

        custom_field_definitions = get_definitions_for_module(organization, module)
        setattr(self, f"_{module}_custom_field_definitions", custom_field_definitions)
        return getattr(self, f"_{module}_custom_field_definitions")

    def validate_custom_fields(self, custom_fields: dict):
        organization = self.get_auth_user_organization()
        custom_field_definitions = get_definitions_for_module(
            organization, module=self.custom_field_module_name
        )

        custom_field_raw_data_errors = []
        for custom_field_definition in custom_field_definitions:
            raw_value = custom_fields.get(custom_field_definition.field_key, "")
            if isinstance(raw_value, str):
                raw_value = raw_value.strip()

            if not raw_value:
                continue

            valid, error_message = _validate_field_value(
                definition=custom_field_definition, raw_value=raw_value
            )
            if not valid:
                custom_field_raw_data_errors.append(error_message)

        if len(custom_field_raw_data_errors) > 0:
            raise serializers.ValidationError(
                f"Following Custom Field data is invalid: {', '.join(custom_field_raw_data_errors)}"
            )

        return custom_fields

    def create_custom_field_entries(self, created_object, custom_fields: dict):
        """Create custom field entries related to the created object.

        Make sure custom_fields are saved through this method only.
        """

        custom_field_definitions = self.get_custom_field_definitions_for_module(
            organization=self.get_auth_user_organization(),
            module=self.custom_field_module_name,
        )

        # Performing required custom fields checks here
        # to allow optional custom fields update
        missing_required_custom_fields = []
        for custom_field_definition in custom_field_definitions:
            custom_field_data = (
                custom_fields.get(custom_field_definition.field_key, "") or ""
            )
            if isinstance(custom_field_data, str):
                custom_field_data = custom_field_data.strip()

            if custom_field_data or (
                not custom_field_data and not custom_field_definition.is_required
            ):
                continue

            missing_required_custom_fields.append(custom_field_definition.field_key)

        if len(missing_required_custom_fields) > 0:
            raise serializers.ValidationError(
                {
                    "custom_fields": "Following Custom Field Keys are required: "
                    + ", ".join(missing_required_custom_fields)
                }
            )

        custom_field_objects: list[CustomFieldValue] = []
        for custom_field_definition in custom_field_definitions:
            custom_field_data = (
                custom_fields.get(custom_field_definition.field_key, "") or ""
            )
            if isinstance(custom_field_data, str):
                custom_field_data = custom_field_data.strip()

            if not custom_field_data:
                continue

            custom_field_objects.append(
                CustomFieldValue(
                    definition=custom_field_definition,
                    entity_uuid=created_object.id,
                    value_text=custom_field_data,
                )
            )

        if custom_field_objects:
            CustomFieldValue.objects.bulk_create(custom_field_objects)

        return created_object

    def update_custom_field_entries(self, updated_object, custom_fields: dict):
        """Update custom field entries related to the updated object.

        Make sure custom_fields are saved through this method only.
        """

        custom_field_definitions = self.get_custom_field_definitions_for_module(
            organization=self.get_auth_user_organization(),
            module=self.custom_field_module_name,
        )

        custom_field_definitions_to_update: dict[CustomFieldDefinition, Any] = {}

        for custom_field_definition in custom_field_definitions:
            custom_field_data = (
                custom_fields.get(custom_field_definition.field_key, "") or ""
            )
            if isinstance(custom_field_data, str):
                custom_field_data = custom_field_data.strip()

            if not custom_field_data:
                continue

            custom_field_definitions_to_update[custom_field_definition] = (
                custom_field_data
            )

        custom_field_values_to_update = CustomFieldValue.objects.filter(
            entity_uuid=updated_object.id,
            definition__in=custom_field_definitions_to_update,
        )

        for custom_field_value in custom_field_values_to_update:
            custom_field_value.value_text = custom_field_definitions_to_update[
                custom_field_value.definition
            ]

        CustomFieldValue.objects.bulk_update(
            custom_field_values_to_update, ["value_text"]
        )

        return updated_object
