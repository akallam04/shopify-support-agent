from app.config import Settings


def test_settings_ignore_keys_that_belong_to_other_tools(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SHOPIFY_STORE_DOMAIN=test.myshopify.com\n"
        "LAMBDA_ROLE_NAME=some-role\n"
        "SHOPIFY_WRITE_TOKEN=not-for-the-app\n"
    )
    settings = Settings(_env_file=env_file)
    assert settings.shopify_store_domain == "test.myshopify.com"
