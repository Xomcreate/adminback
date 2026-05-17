from django.contrib import admin
from .models import Investor, Investment, Withdrawal

admin.site.register(Investor)
admin.site.register(Investment)
admin.site.register(Withdrawal)