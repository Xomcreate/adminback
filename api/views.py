from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.contrib.auth.models import User

from .models import Investor, Investment, Withdrawal
from .serializers import InvestorSerializer, InvestmentSerializer, WithdrawalSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    username = request.data.get("username")
    email    = request.data.get("email")
    password = request.data.get("password")

    if not username or not email or not password:
        return Response({"error": "All fields are required"}, status=400)

    if User.objects.filter(username=username).exists():
        return Response({"error": "Username already taken"}, status=400)

    if User.objects.filter(email=email).exists():
        return Response({"error": "Email already registered"}, status=400)

    user = User.objects.create_user(username=username, email=email, password=password)

    Investor.objects.create(
        user=user,
        name=username,
        email=email,
        phone="",
        role="user",
    )

    return Response({"message": "Account created successfully"}, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def user_dashboard(request):
    # Superuser gets admin role, auto-create profile if missing
    if request.user.is_superuser:
        investor, _ = Investor.objects.get_or_create(
            user=request.user,
            defaults={
                "name": request.user.username,
                "email": request.user.email or f"{request.user.username}@admin.local",
                "role": "admin",
            }
        )
        # Always ensure superuser has admin role
        if investor.role != "admin":
            investor.role = "admin"
            investor.save()
    else:
        try:
            investor = Investor.objects.get(user=request.user)
        except Investor.DoesNotExist:
            return Response({"error": "Investor profile not found"}, status=404)

    return Response({
        "profile": InvestorSerializer(investor).data,
        "investments": InvestmentSerializer(
            Investment.objects.filter(investor=investor), many=True
        ).data,
        "withdrawals": WithdrawalSerializer(
            Withdrawal.objects.filter(investor=investor), many=True
        ).data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def profile_view(request):
    try:
        investor = Investor.objects.get(user=request.user)
    except Investor.DoesNotExist:
        return Response({"error": "Profile not found"}, status=404)
    return Response(InvestorSerializer(investor).data)


class InvestorViewSet(viewsets.ModelViewSet):
    queryset = Investor.objects.all()
    serializer_class = InvestorSerializer
    permission_classes = [IsAuthenticated]


class InvestmentViewSet(viewsets.ModelViewSet):
    queryset = Investment.objects.all()
    serializer_class = InvestmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            return Investment.objects.none()
        if investor.role == "admin":
            return Investment.objects.all()
        return Investment.objects.filter(investor=investor)

    def perform_create(self, serializer):
        investor = Investor.objects.get(user=self.request.user)
        serializer.save(investor=investor)


class WithdrawalViewSet(viewsets.ModelViewSet):
    queryset = Withdrawal.objects.all()
    serializer_class = WithdrawalSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            investor = Investor.objects.get(user=self.request.user)
        except Investor.DoesNotExist:
            return Withdrawal.objects.none()
        if investor.role == "admin":
            return Withdrawal.objects.all()
        return Withdrawal.objects.filter(investor=investor)

    def perform_create(self, serializer):
        investor = Investor.objects.get(user=self.request.user)
        serializer.save(investor=investor)