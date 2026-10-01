from django.db import transaction
from rest_framework import serializers

from custom_fields.serializers import CustomFieldsSerializerFieldMixin
from dashboard.models import Address
from vendors.models import Vendor


class VendorSerializer(CustomFieldsSerializerFieldMixin, serializers.ModelSerializer):
    name = serializers.CharField(required=True)
    email = serializers.EmailField(required=False)
    phone = serializers.CharField(required=False)
    contact_person = serializers.CharField(required=False)
    designation = serializers.CharField(required=False)
    gstin_number = serializers.CharField(required=False)
    description = serializers.CharField(required=False)
    address_line_one = serializers.CharField(required=False)
    address_line_two = serializers.CharField(required=False)
    country = serializers.CharField(required=False)
    state = serializers.CharField(required=False)
    city = serializers.CharField(required=False)
    pin_code = serializers.CharField(required=False)

    CUSTOM_FIELD_MODULE_NAME = "vendor"

    class Meta:
        model = Vendor
        fields = [
            "name",
            "email",
            "phone",
            "contact_person",
            "designation",
            "gstin_number",
            "description",
            "address_line_one",
            "address_line_two",
            "country",
            "state",
            "city",
            "pin_code",
            "custom_fields",
        ]

    def to_internal_value(self, data):
        mutable_data = self.get_mutable_dict(data)
        _data = self.set_custom_fields_internal_value(data=mutable_data)
        return super().to_internal_value(data=_data)

    @transaction.atomic
    def create(self, validated_data):
        custom_fields = validated_data.pop("custom_fields", {}) or {}

        address_fields = {
            "address_line_one": validated_data.pop("address_line_one", None),
            "address_line_two": validated_data.pop("address_line_two", None),
            "country": validated_data.pop("country", None),
            "state": validated_data.pop("state", None),
            "city": validated_data.pop("city", None),
            "pin_code": validated_data.pop("pin_code", None),
        }
        address = Address.objects.create(**address_fields)

        vendor = Vendor.objects.create(
            **validated_data,
            address=address,
            organization=self.context["request"].user.organization,
        )

        self.create_custom_field_entries(
            created_object=vendor, custom_fields=custom_fields
        )
        return vendor

    @transaction.atomic
    def update(self, instance, validated_data):
        custom_fields = validated_data.pop("custom_fields", {}) or {}

        name = validated_data.pop("name", None)
        email = validated_data.pop("email", None)
        phone = validated_data.pop("phone", None)
        contact_person = validated_data.pop("contact_person", None)
        designation = validated_data.pop("designation", None)
        gstin_number = validated_data.pop("gstin_number", None)
        description = validated_data.pop("description", None)
        address_data = {
            "address_line_one": validated_data.pop("address_line_one", None),
            "address_line_two": validated_data.pop("address_line_two", None),
            "country": validated_data.pop("country", None),
            "state": validated_data.pop("state", None),
            "city": validated_data.pop("city", None),
            "pin_code": validated_data.pop("pin_code", None),
        }
        for attributes, value in validated_data.items():
            if value is None:
                continue
            setattr(instance, attributes, value)
        address_instance = instance.address if instance.address else None
        for key, value in address_data.items():
            setattr(address_instance, key, value)
        address_instance.save()
        get_vendor = Vendor.objects.filter(id=instance.id).first()
        if get_vendor:
            vendor = Vendor.objects.filter(id=instance.id).update(
                name=name,
                email=email,
                phone=phone,
                contact_person=contact_person,
                designation=designation,
                gstin_number=gstin_number,
                description=description,
            )

            self.update_custom_field_entries(
                updated_object=instance, custom_fields=custom_fields
            )
            # vendor.save()
        return instance
