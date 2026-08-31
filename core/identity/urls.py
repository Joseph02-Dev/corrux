from django.urls import path

from core.identity import views

urlpatterns = [
    path("csrf/", views.CsrfCookieView.as_view(), name="auth-csrf"),
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("logout/", views.LogoutView.as_view(), name="auth-logout"),
]
