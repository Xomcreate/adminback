from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import api_view

from .models import (
    Investor,
    Investment,
    Withdrawal
)

from .serializers import (
    InvestorSerializer,
    InvestmentSerializer,
    WithdrawalSerializer
)


class InvestorViewSet(viewsets.ModelViewSet):
    queryset = Investor.objects.all()
    serializer_class = InvestorSerializer


class InvestmentViewSet(viewsets.ModelViewSet):
    queryset = Investment.objects.all()
    serializer_class = InvestmentSerializer


class WithdrawalViewSet(viewsets.ModelViewSet):
    queryset = Withdrawal.objects.all()
    serializer_class = WithdrawalSerializer


@api_view(['GET'])
def dashboard_stats(request):

    total_users = Investor.objects.count()

    total_investments = sum(
        investment.amount
        for investment in Investment.objects.all()
    )

    total_withdrawals = sum(
        withdrawal.amount
        for withdrawal in Withdrawal.objects.all()
    )

    blocked_users = Investor.objects.filter(
        blocked=True
    ).count()

    return Response({
        "users": total_users,
        "investments": total_investments,
        "withdrawals": total_withdrawals,
        "blocked_users": blocked_users
    })