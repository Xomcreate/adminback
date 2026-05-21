from rest_framework import serializers
from .models import Investor, Investment, Withdrawal


class InvestorSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Investor
        fields = "__all__"


class InvestmentSerializer(serializers.ModelSerializer):
    class Meta:
        model             = Investment
        fields            = "__all__"
        read_only_fields  = ["investor"]


class WithdrawalSerializer(serializers.ModelSerializer):
    class Meta:
        model            = Withdrawal
        fields           = "__all__"
        read_only_fields = ["investor"]