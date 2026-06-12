from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    InvestorViewSet,
    InvestmentViewSet,
    WithdrawalViewSet,
    DepositViewSet,
    ReferralViewSet,
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
    change_password,
    forgot_password,
    reset_password_confirm,
)

router = DefaultRouter()
router.register("investors",   InvestorViewSet,   basename="investor")
router.register("investments", InvestmentViewSet, basename="investment")
router.register("withdrawals", WithdrawalViewSet, basename="withdrawal")
router.register("deposits",    DepositViewSet,    basename="deposit")
router.register("referrals",   ReferralViewSet,   basename="referral")

# FIX: Manually wire my-stats BEFORE including router URLs so Django never
# tries to resolve "my-stats" as a <pk> integer lookup.
referral_my_stats = ReferralViewSet.as_view({"get": "my_stats"})

urlpatterns = [
    # ── Referral stats — MUST come before router include ──────────────────
    path("referrals/my-stats/", referral_my_stats, name="referral-my-stats"),

    # ── Router ────────────────────────────────────────────────────────────
    path("", include(router.urls)),

    # ── Auth ──────────────────────────────────────────────────────────────
    path("register/",              register),
    path("change-password/",       change_password),
    path("forgot-password/",       forgot_password),
    path("reset-password/confirm/", reset_password_confirm),

    # ── Dashboard / profile ───────────────────────────────────────────────
    path("user-dashboard/",  user_dashboard),
    path("profile/",         profile_view),
    path("dashboard-stats/", dashboard_stats),

    # ── Users ─────────────────────────────────────────────────────────────
    path("users/",                 all_users),
    path("users/<int:pk>/delete/", delete_user),

    # ── Investments ───────────────────────────────────────────────────────
    path("top-investors/",                   top_investors),
    path("investments/<int:pk>/approve/",    approve_investment),
    path("investments/<int:pk>/add_profit/", add_profit),

    # ── Misc ──────────────────────────────────────────────────────────────
    path("trigger-roi/", trigger_roi),
]