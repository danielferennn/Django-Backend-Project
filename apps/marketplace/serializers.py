from rest_framework import serializers
from .models import Store, Product, Transaction
from apps.users.serializers import UserDetailSerializer

class StoreSerializer(serializers.ModelSerializer):
    owner = UserDetailSerializer(read_only=True)

    class Meta:
        model = Store
        fields = ['id', 'owner', 'name', 'description']

class ProductSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = Product
        fields = ['id', 'store', 'name', 'price', 'stock', 'description', 'image']
        read_only_fields = ['store']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if instance.image:
            url = instance.image.url
            data['image'] = request.build_absolute_uri(url) if request else url
        else:
            data['image'] = None
        return data

class TransactionSerializer(serializers.ModelSerializer):
    buyer = UserDetailSerializer(read_only=True)
    product = ProductSerializer(read_only=True)

    class Meta:
        model = Transaction
        fields = '__all__'

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')

        def absolute(field_name):
            file_field = getattr(instance, field_name)
            if file_field:
                url = file_field.url
                return request.build_absolute_uri(url) if request else url
            return None

        data['payment_proof'] = absolute('payment_proof')
        data['qris_image'] = absolute('qris_image')
        return data
