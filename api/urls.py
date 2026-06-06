from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    InvestorViewSet,
    InvestmentViewSet,
    DepositViewSet,
    WithdrawalViewSet,
    user_dashboard,
    profile_view,
    register,
    dashboard_stats,
    all_users,
    top_investors,
    approve_investment,
    approve_deposit,
    reject_deposit,
    add_profit,
    delete_user,
    trigger_roi,
    referral_stats,
)

router = DefaultRouter()
router.register("investors",   InvestorViewSet,   basename="investor")
router.register("investments", InvestmentViewSet, basename="investment")
router.register("deposits",    DepositViewSet,    basename="deposit")
router.register("withdrawals", WithdrawalViewSet, basename="withdrawal")

urlpatterns = [
    path("", include(router.urls)),

    # Auth
    path("register/",                              register),

    # Dashboards
    path("user-dashboard/",                        user_dashboard),
    path("profile/",                               profile_view),
    path("dashboard-stats/",                       dashboard_stats),

    # Users
    path("users/",                                 all_users),
    path("users/<int:pk>/delete/",                 delete_user),
    path("top-investors/",                         top_investors),

    # Investment admin actions
    path("investments/<int:pk>/approve/",          approve_investment),
    path("investments/<int:pk>/add_profit/",       add_profit),

    # Deposit admin actions
    path("deposits/<int:pk>/approve/",             approve_deposit),
    path("deposits/<int:pk>/reject/",              reject_deposit),

    # Referrals
    path("referrals/",                             referral_stats),

    # ROI webhook (cron-job.org fallback)
    path("trigger-roi/",                           trigger_roi),
]