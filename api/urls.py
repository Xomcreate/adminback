from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    InvestorViewSet,
    InvestmentViewSet,
    WithdrawalViewSet,
    dashboard_stats
)

router = DefaultRouter()

router.register("investors", InvestorViewSet)
router.register("investments", InvestmentViewSet)
router.register("withdrawals", WithdrawalViewSet)

urlpatterns = [
    path("", include(router.urls)),
    path("dashboard-stats/", dashboard_stats),
]