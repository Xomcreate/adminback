from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    InvestorViewSet,
    InvestmentViewSet,
    WithdrawalViewSet,
    user_dashboard,
    profile_view,
    register,
    dashboard_stats,
    all_users,
    top_investors,
    approve_investment,
    add_profit,
    delete_user,
    trigger_roi,
    change_password,        # ✅ New
    forgot_password,        # ✅ New
    reset_password_confirm, # ✅ New
)

router = DefaultRouter()
router.register("investors",   InvestorViewSet,   basename="investor")
router.register("investments", InvestmentViewSet, basename="investment")
router.register("withdrawals", WithdrawalViewSet, basename="withdrawal")

urlpatterns = [
    path("", include(router.urls)),
    path("register/",                          register),
    path("user-dashboard/",                    user_dashboard),
    path("profile/",                           profile_view),
    path("dashboard-stats/",                   dashboard_stats),
    path("users/",                             all_users),
    path("users/<int:pk>/delete/",             delete_user),
    path("top-investors/",                     top_investors),
    path("investments/<int:pk>/approve/",      approve_investment),
    path("investments/<int:pk>/add_profit/",   add_profit),
    path("trigger-roi/",                       trigger_roi),
    path("change-password/",                   change_password),        # ✅ New
    path("forgot-password/",                   forgot_password),        # ✅ New
    path("reset-password/confirm/",            reset_password_confirm), # ✅ New
]