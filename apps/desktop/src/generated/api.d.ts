export interface paths {
    "/artifacts/{artifact_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Download Artifact */
        get: operations["download_artifact_artifacts__artifact_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/backups": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Backup */
        post: operations["create_backup_backups_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/discord": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Discord Status */
        get: operations["discord_status_channels_discord_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/discord/credential": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Discord Credential */
        put: operations["set_discord_credential_channels_discord_credential_put"];
        post?: never;
        /** Clear Discord Credential */
        delete: operations["clear_discord_credential_channels_discord_credential_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/discord/deliveries/{message_id}/reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reconcile Discord Delivery */
        post: operations["reconcile_discord_delivery_channels_discord_deliveries__message_id__reconcile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/discord/links": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Revoke Discord Links */
        delete: operations["revoke_discord_links_channels_discord_links_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/discord/pairing": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Discord Pairing */
        post: operations["create_discord_pairing_channels_discord_pairing_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/email": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Email Status */
        get: operations["email_status_channels_email_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/email/credential": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Email Credential */
        put: operations["set_email_credential_channels_email_credential_put"];
        post?: never;
        /** Clear Email Credential */
        delete: operations["clear_email_credential_channels_email_credential_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/email/deliveries/{uid_validity}/{uid}/reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reconcile Email Delivery */
        post: operations["reconcile_email_delivery_channels_email_deliveries__uid_validity___uid__reconcile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/identity-bindings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Channel Identity Bindings */
        get: operations["list_channel_identity_bindings_channels_identity_bindings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/identity-links": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Channel Identity Link */
        post: operations["create_channel_identity_link_channels_identity_links_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/identity-links/{link_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Revoke Channel Identity Link */
        delete: operations["revoke_channel_identity_link_channels_identity_links__link_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/identity-links/{link_id}/members": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Add Channel Identity Link Member */
        post: operations["add_channel_identity_link_member_channels_identity_links__link_id__members_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/telegram": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Telegram Status */
        get: operations["telegram_status_channels_telegram_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/telegram/credential": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Telegram Credential */
        put: operations["set_telegram_credential_channels_telegram_credential_put"];
        post?: never;
        /** Clear Telegram Credential */
        delete: operations["clear_telegram_credential_channels_telegram_credential_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/telegram/deliveries/{update_id}/reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reconcile Telegram Delivery */
        post: operations["reconcile_telegram_delivery_channels_telegram_deliveries__update_id__reconcile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/telegram/linked-users": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Telegram Linked Users */
        get: operations["list_telegram_linked_users_channels_telegram_linked_users_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/telegram/links": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Revoke Telegram Links */
        delete: operations["revoke_telegram_links_channels_telegram_links_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/telegram/pairing": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Telegram Pairing */
        post: operations["create_telegram_pairing_channels_telegram_pairing_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/webhook": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Webhook Status */
        get: operations["webhook_status_channels_webhook_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/channels/webhook/credential": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Webhook Credential */
        post: operations["create_webhook_credential_channels_webhook_credential_post"];
        /** Delete Webhook Credential */
        delete: operations["delete_webhook_credential_channels_webhook_credential_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/chat/acp-sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Acp Chat Sessions */
        get: operations["list_acp_chat_sessions_chat_acp_sessions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/chat/acp-sessions/{session_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Acp Chat Session */
        delete: operations["delete_acp_chat_session_chat_acp_sessions__session_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/chat/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Cli Chat Sessions */
        get: operations["list_cli_chat_sessions_chat_sessions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/chat/sessions/{session_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Cli Chat Session */
        get: operations["get_cli_chat_session_chat_sessions__session_id__get"];
        put?: never;
        /** Ensure Cli Chat Session */
        post: operations["ensure_cli_chat_session_chat_sessions__session_id__post"];
        /** Delete Cli Chat Session */
        delete: operations["delete_cli_chat_session_chat_sessions__session_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/chat/sessions/{session_id}/turn": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Append Cli Chat Turn */
        post: operations["append_cli_chat_turn_chat_sessions__session_id__turn_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/chat/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Stream Chat */
        post: operations["stream_chat_chat_stream_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/execution/distributions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Execution Distributions */
        get: operations["execution_distributions_execution_distributions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/execution/readiness": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check Execution Readiness */
        post: operations["check_execution_readiness_execution_readiness_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/extensions/packages/data": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Extension Package Data */
        get: operations["list_extension_package_data_extensions_packages_data_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/extensions/packages/data/remove": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Remove Extension Package Data */
        post: operations["remove_extension_package_data_extensions_packages_data_remove_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/extensions/packages/discover": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Discover Extension Packages */
        post: operations["discover_extension_packages_extensions_packages_discover_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/extensions/packages/import-mcp-server": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Import Extension Mcp Server */
        post: operations["import_extension_mcp_server_extensions_packages_import_mcp_server_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/extensions/packages/import-skill": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Import Extension Skill */
        post: operations["import_extension_skill_extensions_packages_import_skill_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/extensions/packages/install": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Install Extension Package */
        post: operations["install_extension_package_extensions_packages_install_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/extensions/packages/installed": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Discover Installed Extension Packages */
        get: operations["discover_installed_extension_packages_extensions_packages_installed_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/extensions/packages/remove": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Remove Extension Package */
        post: operations["remove_extension_package_extensions_packages_remove_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Mcp Servers */
        get: operations["list_mcp_servers_mcp_servers_get"];
        put?: never;
        /** Create Mcp Server */
        post: operations["create_mcp_server_mcp_servers_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Mcp Server */
        delete: operations["delete_mcp_server_mcp_servers__server_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/connect": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Connect Mcp Server */
        post: operations["connect_mcp_server_mcp_servers__server_id__connect_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/disconnect": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Disconnect Mcp Server */
        post: operations["disconnect_mcp_server_mcp_servers__server_id__disconnect_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/oauth": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Mcp Oauth */
        delete: operations["delete_mcp_oauth_mcp_servers__server_id__oauth_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/prompts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** List Mcp Prompts */
        post: operations["list_mcp_prompts_mcp_servers__server_id__prompts_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/prompts/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Mcp Prompt */
        post: operations["preview_mcp_prompt_mcp_servers__server_id__prompts_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/resource-templates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** List Mcp Resource Templates */
        post: operations["list_mcp_resource_templates_mcp_servers__server_id__resource_templates_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/resource-templates/read": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Read Mcp Resource Template */
        post: operations["read_mcp_resource_template_mcp_servers__server_id__resource_templates_read_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/resources": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** List Mcp Resources */
        post: operations["list_mcp_resources_mcp_servers__server_id__resources_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/resources/notifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Drain Mcp Resource Notifications */
        post: operations["drain_mcp_resource_notifications_mcp_servers__server_id__resources_notifications_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/resources/read": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Read Mcp Resource */
        post: operations["read_mcp_resource_mcp_servers__server_id__resources_read_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/resources/subscription": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Set Mcp Resource Subscription */
        post: operations["set_mcp_resource_subscription_mcp_servers__server_id__resources_subscription_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/secrets/{env_name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Mcp Secret */
        put: operations["set_mcp_secret_mcp_servers__server_id__secrets__env_name__put"];
        post?: never;
        /** Delete Mcp Secret */
        delete: operations["delete_mcp_secret_mcp_servers__server_id__secrets__env_name__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/mcp/servers/{server_id}/tools/{tool_name}/permission": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Mcp Tool Permission */
        put: operations["set_mcp_tool_permission_mcp_servers__server_id__tools__tool_name__permission_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/memory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Search Memory */
        get: operations["search_memory_memory_get"];
        put?: never;
        /** Create Memory */
        post: operations["create_memory_memory_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/memory/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Export Memory */
        get: operations["export_memory_memory_export_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/memory/knowledge": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Sourced Knowledge */
        post: operations["create_sourced_knowledge_memory_knowledge_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/memory/knowledge/{memory_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Edit Sourced Knowledge */
        put: operations["edit_sourced_knowledge_memory_knowledge__memory_id__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/memory/{memory_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Edit Memory */
        post: operations["edit_memory_memory__memory_id__post"];
        /** Delete Memory */
        delete: operations["delete_memory_memory__memory_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Models */
        get: operations["models_models_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/models/load": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Load Model */
        post: operations["load_model_models_load_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/models/unload": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Unload Model */
        post: operations["unload_model_models_unload_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Providers */
        get: operations["providers_providers_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers/brave-search/credential": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Brave Search Credential Status */
        get: operations["brave_search_credential_status_providers_brave_search_credential_get"];
        /** Set Brave Search Credential */
        put: operations["set_brave_search_credential_providers_brave_search_credential_put"];
        post?: never;
        /** Delete Brave Search Credential */
        delete: operations["delete_brave_search_credential_providers_brave_search_credential_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers/diagnostics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Diagnose Model */
        post: operations["diagnose_model_providers_diagnostics_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers/lm-studio/config": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Lm Studio Config */
        get: operations["lm_studio_config_providers_lm_studio_config_get"];
        /** Set Lm Studio Endpoint */
        put: operations["set_lm_studio_endpoint_providers_lm_studio_config_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers/lm-studio/credential": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Lm Studio Credential Status */
        get: operations["lm_studio_credential_status_providers_lm_studio_credential_get"];
        /** Set Lm Studio Credential */
        put: operations["set_lm_studio_credential_providers_lm_studio_credential_put"];
        post?: never;
        /** Delete Lm Studio Credential */
        delete: operations["delete_lm_studio_credential_providers_lm_studio_credential_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers/lm-studio/selection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Lm Studio Model */
        put: operations["set_lm_studio_model_providers_lm_studio_selection_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers/openai-compatible/config": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Compatible Provider Config */
        get: operations["compatible_provider_config_providers_openai_compatible_config_get"];
        /** Set Compatible Provider Config */
        put: operations["set_compatible_provider_config_providers_openai_compatible_config_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers/openai-compatible/credential": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Compatible Provider Credential Status */
        get: operations["compatible_provider_credential_status_providers_openai_compatible_credential_get"];
        /** Set Compatible Provider Credential */
        put: operations["set_compatible_provider_credential_providers_openai_compatible_credential_put"];
        post?: never;
        /** Delete Compatible Provider Credential */
        delete: operations["delete_compatible_provider_credential_providers_openai_compatible_credential_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers/openai-compatible/selection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Compatible Provider Model */
        put: operations["set_compatible_provider_model_providers_openai_compatible_selection_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/providers/token-count": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Token Count */
        post: operations["token_count_providers_token_count_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Run History */
        get: operations["run_history_runs_get"];
        put?: never;
        /** Create Run */
        post: operations["create_run_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/latest-unfinished": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Latest Unfinished Run */
        get: operations["get_latest_unfinished_run_runs_latest_unfinished_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Run */
        get: operations["get_run_runs__run_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/artifacts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Artifacts */
        get: operations["artifacts_runs__run_id__artifacts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Run */
        post: operations["cancel_run_runs__run_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Run Events */
        get: operations["get_run_events_runs__run_id__events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/events/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Stream Run Events */
        get: operations["stream_run_events_runs__run_id__events_stream_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Export Run Trajectory */
        get: operations["export_run_trajectory_runs__run_id__export_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/mcp-reconciliations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Mcp Reconciliations */
        get: operations["get_mcp_reconciliations_runs__run_id__mcp_reconciliations_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/pause": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Pause Run */
        post: operations["pause_run_runs__run_id__pause_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/plan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Plan */
        get: operations["get_plan_runs__run_id__plan_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/plan/dispatch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Dispatch Plan */
        post: operations["dispatch_plan_runs__run_id__plan_dispatch_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resume Run */
        post: operations["resume_run_runs__run_id__resume_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide Run */
        post: operations["decide_run_runs__run_id__review_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/tasks/{task_id}/candidate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Candidate */
        get: operations["get_candidate_runs__run_id__tasks__task_id__candidate_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/tasks/{task_id}/integration-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Task Integration */
        post: operations["preview_task_integration_runs__run_id__tasks__task_id__integration_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/tasks/{task_id}/recovery": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Task Recovery */
        get: operations["get_task_recovery_runs__run_id__tasks__task_id__recovery_get"];
        put?: never;
        /** Decide Task Recovery */
        post: operations["decide_task_recovery_runs__run_id__tasks__task_id__recovery_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/tasks/{task_id}/recovery/actions/{model_call_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Attach Saved Task Action */
        post: operations["attach_saved_task_action_runs__run_id__tasks__task_id__recovery_actions__model_call_id__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/tasks/{task_id}/recovery/commands/{model_call_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reconcile Task Command */
        post: operations["reconcile_task_command_runs__run_id__tasks__task_id__recovery_commands__model_call_id__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/tasks/{task_id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide Candidate */
        post: operations["decide_candidate_runs__run_id__tasks__task_id__review_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/tools/{call_id}/mcp-reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reconcile Mcp Effect */
        post: operations["reconcile_mcp_effect_runs__run_id__tools__call_id__mcp_reconcile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/tools/{call_id}/result": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Public Tool Result */
        get: operations["get_public_tool_result_runs__run_id__tools__call_id__result_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/tools/{call_id}/workspace-write-reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reconcile Workspace Write Not Applied */
        post: operations["reconcile_workspace_write_not_applied_runs__run_id__tools__call_id__workspace_write_reconcile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Run Usage */
        get: operations["get_run_usage_runs__run_id__usage_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/validation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Project Validation */
        get: operations["get_project_validation_runs__run_id__validation_get"];
        put?: never;
        /** Run Project Validation */
        post: operations["run_project_validation_runs__run_id__validation_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/validation/{call_id}/reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reconcile Project Validation */
        post: operations["reconcile_project_validation_runs__run_id__validation__call_id__reconcile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/workspace-write-reconciliations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Workspace Write Reconciliations */
        get: operations["get_workspace_write_reconciliations_runs__run_id__workspace_write_reconciliations_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/schedules": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Schedules */
        get: operations["schedules_schedules_get"];
        put?: never;
        /** Create Schedule */
        post: operations["create_schedule_schedules_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/schedules/{schedule_id}/enabled": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Schedule Enabled */
        put: operations["set_schedule_enabled_schedules__schedule_id__enabled_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/schedules/{schedule_id}/executions/{execution_id}/delivery/reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reconcile Schedule Delivery */
        post: operations["reconcile_schedule_delivery_schedules__schedule_id__executions__execution_id__delivery_reconcile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/shutdown": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Shutdown */
        post: operations["shutdown_shutdown_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/skills": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Skills */
        get: operations["skills_skills_get"];
        put?: never;
        /** Create Skill */
        post: operations["create_skill_skills_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/skills/{skill_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Skill */
        get: operations["get_skill_skills__skill_id__get"];
        put?: never;
        post?: never;
        /** Delete Skill */
        delete: operations["delete_skill_skills__skill_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/skills/{skill_id}/lifecycle": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Skill Lifecycle */
        get: operations["skill_lifecycle_skills__skill_id__lifecycle_get"];
        put?: never;
        /** Decide Skill Lifecycle */
        post: operations["decide_skill_lifecycle_skills__skill_id__lifecycle_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/skills/{skill_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revise Skill */
        post: operations["revise_skill_skills__skill_id__versions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/skills/{skill_id}/versions/{version}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Skill Version */
        get: operations["skill_version_skills__skill_id__versions__version__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/skills/{skill_id}/versions/{version}/evaluations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evaluate Skill */
        post: operations["evaluate_skill_skills__skill_id__versions__version__evaluations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/skills/{skill_id}/versions/{version}/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Export Skill Package */
        get: operations["export_skill_package_skills__skill_id__versions__version__export_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/skills/{skill_id}/versions/{version}/trials": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Eligible Skill Trials */
        get: operations["eligible_skill_trials_skills__skill_id__versions__version__trials_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/specialists/profiles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Specialist Profiles */
        get: operations["list_specialist_profiles_specialists_profiles_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/specialists/profiles/{role}/identity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Specialist Identity */
        put: operations["set_specialist_identity_specialists_profiles__role__identity_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/specialists/profiles/{role}/instructions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Specialist Instructions */
        put: operations["set_specialist_instructions_specialists_profiles__role__instructions_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/specialists/profiles/{role}/memory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Specialist Memory */
        get: operations["list_specialist_memory_specialists_profiles__role__memory_get"];
        put?: never;
        /** Save Specialist Memory */
        post: operations["save_specialist_memory_specialists_profiles__role__memory_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/specialists/profiles/{role}/memory/{memory_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Specialist Memory */
        delete: operations["delete_specialist_memory_specialists_profiles__role__memory__memory_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/specialists/profiles/{role}/route": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Specialist Route */
        put: operations["set_specialist_route_specialists_profiles__role__route_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/webhooks/inbound": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Receive Webhook */
        post: operations["receive_webhook_webhooks_inbound_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** ArtifactRecord */
        ArtifactRecord: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Id */
            id: string;
            /** Media Type */
            media_type: string;
            /** Provenance */
            provenance: string;
            /** Run Id */
            run_id: string;
            /** Sha256 */
            sha256: string;
            /** Size Bytes */
            size_bytes: number;
        };
        /** CLIChatMessageRecord */
        CLIChatMessageRecord: {
            /** Content */
            content: string;
            /**
             * Role
             * @enum {string}
             */
            role: "user" | "assistant";
            /** Run Id */
            run_id: string;
        };
        /** CLIChatSessionRecord */
        CLIChatSessionRecord: {
            /** Messages */
            messages: components["schemas"]["CLIChatMessageRecord"][];
            /**
             * Mode
             * @enum {string}
             */
            mode: "observe" | "workspace";
            /** Model */
            model: string;
            /** Session Id */
            session_id: string;
            /** Workspace */
            workspace: string | null;
        };
        /** CLIChatSessionSettings */
        CLIChatSessionSettings: {
            /**
             * Mode
             * @enum {string}
             */
            mode: "observe" | "workspace";
            /** Model */
            model: string;
            /** Workspace */
            workspace?: string | null;
        };
        /** CLIChatSessionSummary */
        CLIChatSessionSummary: {
            /**
             * Mode
             * @enum {string}
             */
            mode: "observe" | "workspace";
            /** Model */
            model: string;
            /** Session Id */
            session_id: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Workspace */
            workspace: string | null;
        };
        /** CLIChatTurnRequest */
        CLIChatTurnRequest: {
            /** Assistant Message */
            assistant_message: string;
            /**
             * Mode
             * @enum {string}
             */
            mode: "observe" | "workspace";
            /** Model */
            model: string;
            /** Run Id */
            run_id: string;
            /** User Message */
            user_message: string;
            /** Workspace */
            workspace?: string | null;
        };
        /** ChannelIdentityBinding */
        ChannelIdentityBinding: {
            /** Account Id */
            account_id: string;
            /** Authorized */
            authorized: boolean;
            /** Channel */
            channel: string;
            /** External Identity */
            external_identity: string;
            /** Id */
            id: string;
            /** Identity Link Id */
            identity_link_id: string | null;
            /** Link Created At */
            link_created_at: string | null;
            /** Session Channel */
            session_channel: string;
            /** Session Id */
            session_id: string;
            /** Title */
            title: string | null;
        };
        /** ChannelIdentityLinkMemberRequest */
        ChannelIdentityLinkMemberRequest: {
            /** Binding Id */
            binding_id: string;
            /**
             * Confirmed
             * @constant
             */
            confirmed: true;
        };
        /** ChannelIdentityLinkMemberResponse */
        ChannelIdentityLinkMemberResponse: {
            /** Binding Id */
            binding_id: string;
            /** Link Id */
            link_id: string;
            /** Shared Session Id */
            shared_session_id: string;
        };
        /** ChannelIdentityLinkRecord */
        ChannelIdentityLinkRecord: {
            /** Id */
            id: string;
            /** Shared History Binding Id */
            shared_history_binding_id: string;
        };
        /** ChannelIdentityLinkRequest */
        ChannelIdentityLinkRequest: {
            /** Binding Ids */
            binding_ids: string[];
            /** Shared History Binding Id */
            shared_history_binding_id: string;
        };
        /** ChatRequest */
        ChatRequest: {
            /** Messages */
            messages: {
                [key: string]: unknown;
            }[];
            /** Model */
            model: string;
        };
        /** CommandGrant */
        CommandGrant: {
            /**
             * Backend
             * @default wsl-bubblewrap-v1
             * @constant
             */
            backend: "wsl-bubblewrap-v1";
            /** Distribution */
            distribution: string;
            /**
             * Max Seconds
             * @default 30
             */
            max_seconds: number;
            /** Profile Sha256 */
            profile_sha256: string;
        };
        /** CompatibleProviderConfigRecord */
        CompatibleProviderConfigRecord: {
            /** Endpoint */
            endpoint: string | null;
            /** Selected Model */
            selected_model: string | null;
        };
        /** CompatibleProviderConfigRequest */
        CompatibleProviderConfigRequest: {
            /** Endpoint */
            endpoint: string;
        };
        /** CompatibleProviderSelectionRequest */
        CompatibleProviderSelectionRequest: {
            /** Model */
            model?: string | null;
        };
        /** CreateMCPServerRequest */
        CreateMCPServerRequest: {
            /** Args */
            args?: string[];
            /**
             * Command
             * @default
             */
            command: string;
            /** Cwd */
            cwd?: string | null;
            /** Endpoint */
            endpoint?: string | null;
            /** Name */
            name: string;
            /** Secret Env */
            secret_env?: string[];
            /**
             * Transport
             * @default stdio
             * @enum {string}
             */
            transport: "stdio" | "streamable_http";
        };
        /** CreateRunRequest */
        CreateRunRequest: {
            /** Acceptance Criteria */
            acceptance_criteria?: string[];
            /**
             * Allow Procedure Drafting
             * @default false
             */
            allow_procedure_drafting: boolean;
            /**
             * Allow Procedure Resource Removal
             * @default false
             */
            allow_procedure_resource_removal: boolean;
            /**
             * Allow Procedure Resource Revision
             * @default false
             */
            allow_procedure_resource_revision: boolean;
            /**
             * Allow Procedure Revision
             * @default false
             */
            allow_procedure_revision: boolean;
            /**
             * Approved Browser
             * @default false
             */
            approved_browser: boolean;
            /** Approved Mcp Tools */
            approved_mcp_tools?: string[];
            /** Approved Web Hosts */
            approved_web_hosts?: string[];
            /**
             * Approved Web Search
             * @default false
             */
            approved_web_search: boolean;
            /** Audio */
            audio?: string | null;
            /** Client Request Id */
            client_request_id?: string | null;
            command_grant?: components["schemas"]["CommandGrant"] | null;
            /** Document */
            document?: string | null;
            /** File Checks */
            file_checks?: components["schemas"]["FileContentCheck"][];
            /** Image */
            image?: string | null;
            /**
             * Max Iterations
             * @default 8
             */
            max_iterations: number;
            /** Max Output Tokens */
            max_output_tokens?: number | null;
            /**
             * Max Seconds
             * @default 900
             */
            max_seconds: number;
            /** Max Total Tokens */
            max_total_tokens?: number | null;
            /**
             * Mode
             * @default observe
             * @enum {string}
             */
            mode: "observe" | "workspace" | "authorized_automation";
            /** Model */
            model?: string | null;
            /** Objective */
            objective: string;
            /**
             * Pre Inference Token Reservations
             * @description Require exact prompt counting and a durable token hold before each inference.
             * @default false
             */
            pre_inference_token_reservations: boolean;
            procedure_trial?: components["schemas"]["SkillTrial"] | null;
            /** Transcription Url */
            transcription_url?: string | null;
            /** Workspace */
            workspace?: string | null;
        };
        /** CreateScheduleRequest */
        CreateScheduleRequest: {
            /** Acceptance Criteria */
            acceptance_criteria?: string[];
            /**
             * Frequency
             * @enum {string}
             */
            frequency: "once" | "daily" | "weekly";
            /**
             * Max Iterations
             * @default 8
             */
            max_iterations: number;
            /**
             * Max Seconds
             * @default 900
             */
            max_seconds: number;
            /**
             * Missed Run Policy
             * @default skip
             * @enum {string}
             */
            missed_run_policy: "skip" | "run_once";
            /** Model */
            model: string;
            /** Objective */
            objective: string;
            /** Quiet Hours */
            quiet_hours?: string | null;
            /** Start At */
            start_at: string;
            /** Telegram User Id */
            telegram_user_id?: string | null;
            /** Timezone */
            timezone: string;
            /** Workspace */
            workspace?: string | null;
        };
        /** DiscordCredentialRequest */
        DiscordCredentialRequest: {
            /**
             * Token
             * Format: password
             */
            token: string;
        };
        /** DiscordCredentialStatus */
        DiscordCredentialStatus: {
            /** Bot Id */
            bot_id: string | null;
            /** Bot Username */
            bot_username: string | null;
            /** Configured */
            configured: boolean;
            /** Deliveries */
            deliveries: {
                [key: string]: number;
            };
            /** Linked Users */
            linked_users: number;
            /** Processed Messages */
            processed_messages: number;
            /** Unknown Deliveries */
            unknown_deliveries: components["schemas"]["DiscordUnknownDelivery"][];
        };
        /** DiscordDeliveryReconciliation */
        DiscordDeliveryReconciliation: {
            /**
             * Confirmed Destination Checked
             * @constant
             */
            confirmed_destination_checked: true;
            /**
             * Resolution
             * @enum {string}
             */
            resolution: "delivered" | "retry";
        };
        /** DiscordLinkRevocation */
        DiscordLinkRevocation: {
            /** Linked Users */
            linked_users: number;
            /** Revoked Users */
            revoked_users: number;
        };
        /** DiscordPairingCode */
        DiscordPairingCode: {
            /** Code */
            code: string;
            /** Expires At */
            expires_at: string;
        };
        /** DiscordUnknownDelivery */
        DiscordUnknownDelivery: {
            /** Message Id */
            message_id: string;
            /** Run Id */
            run_id: string | null;
            /** Updated At */
            updated_at: string;
            /** User Id */
            user_id: string | null;
        };
        /** DistributionInventory */
        DistributionInventory: {
            /** Distributions */
            distributions: components["schemas"]["InstalledDistribution"][];
            /**
             * State
             * @enum {string}
             */
            state: "available" | "unsupported" | "unavailable";
        };
        /** EmailCredentialRequest */
        EmailCredentialRequest: {
            /** Address */
            address: string;
            /** Allowed Senders */
            allowed_senders: string[];
            /** Authserv Id */
            authserv_id: string;
            /** Imap Host */
            imap_host: string;
            /**
             * Imap Port
             * @default 993
             */
            imap_port: number;
            /**
             * Password
             * Format: password
             */
            password: string;
            /** Smtp Host */
            smtp_host: string;
            /**
             * Smtp Port
             * @default 587
             */
            smtp_port: number;
            /**
             * Smtp Security
             * @default starttls
             * @enum {string}
             */
            smtp_security: "starttls" | "tls";
        };
        /** EmailCredentialStatus */
        EmailCredentialStatus: {
            /** Address */
            address: string | null;
            /** Configured */
            configured: boolean;
            /** Deliveries */
            deliveries: {
                [key: string]: number;
            };
            /** Processed Messages */
            processed_messages: number;
            /** Unknown Deliveries */
            unknown_deliveries: components["schemas"]["EmailUnknownDelivery"][];
        };
        /** EmailDeliveryReconciliation */
        EmailDeliveryReconciliation: {
            /**
             * Confirmed Destination Checked
             * @constant
             */
            confirmed_destination_checked: true;
            /**
             * Resolution
             * @enum {string}
             */
            resolution: "delivered" | "retry";
        };
        /** EmailUnknownDelivery */
        EmailUnknownDelivery: {
            /** Run Id */
            run_id: string | null;
            /** Sender */
            sender: string | null;
            /** Uid */
            uid: string;
            /** Uid Validity */
            uid_validity: string;
            /** Updated At */
            updated_at: string;
        };
        /** EvaluationRecord */
        EvaluationRecord: {
            /**
             * Actor
             * @constant
             */
            actor: "local-user";
            /** Confirmed Criteria */
            confirmed_criteria: number[];
            /** Criteria */
            criteria: string[];
            /**
             * Evaluated At
             * Format: date-time
             */
            evaluated_at: string;
            /** Id */
            id: string;
            /** Rationale */
            rationale: string;
            /** Snapshot Sha256 */
            snapshot_sha256: string;
            /** Trials */
            trials: components["schemas"]["EvaluationTrialRecord"][];
        };
        /** EvaluationTrialRecord */
        EvaluationTrialRecord: {
            /** Case Description */
            case_description: string;
            /** Final Review Sequence */
            final_review_sequence: number;
            /** Objective */
            objective: string;
            /** Run Id */
            run_id: string;
            /** Run Revision */
            run_revision: number;
        };
        /** ExecutionPolicy */
        ExecutionPolicy: {
            /**
             * Cpu Percent
             * @default 50
             * @constant
             */
            cpu_percent: 50;
            /**
             * Max Active Executions Per Distribution
             * @default 2
             * @constant
             */
            max_active_executions_per_distribution: 2;
            /**
             * Max Seconds
             * @default 30
             * @constant
             */
            max_seconds: 30;
            /**
             * Memory Bytes
             * @default 134217728
             * @constant
             */
            memory_bytes: 134217728;
            /**
             * Network
             * @default denied
             * @constant
             */
            network: "denied";
            /**
             * Output Bytes
             * @default 1048576
             * @constant
             */
            output_bytes: 1048576;
            /**
             * Private Workspace Bytes
             * @default 33554432
             * @constant
             */
            private_workspace_bytes: 33554432;
            /**
             * Tasks
             * @default 32
             * @constant
             */
            tasks: 32;
            /**
             * Workspace Changes
             * @default private_copy_only
             * @constant
             */
            workspace_changes: "private_copy_only";
        };
        /** FileContentCheck */
        FileContentCheck: {
            /** Expected Content */
            expected_content: string;
            /** Path */
            path: string;
        };
        /** FunctionCall */
        FunctionCall: {
            /** Arguments */
            arguments: string;
            /** Name */
            name: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** InstalledDistribution */
        InstalledDistribution: {
            /** Name */
            name: string;
            /** Supported By Profile */
            supported_by_profile: boolean;
        };
        /** IntegrationFile */
        IntegrationFile: {
            /** Base Sha256 */
            base_sha256: string | null;
            /** Conflict */
            conflict: boolean;
            /** Current Excerpt */
            current_excerpt: string;
            /** Current Excerpt Truncated */
            current_excerpt_truncated: boolean;
            /** Current Sha256 */
            current_sha256: string | null;
            /** Diff */
            diff: string;
            /** Diff Truncated */
            diff_truncated: boolean;
            /** Path */
            path: string;
            /** Proposed Sha256 */
            proposed_sha256: string | null;
        };
        /** IntegrationPreview */
        IntegrationPreview: {
            /** Changes */
            changes: components["schemas"]["IntegrationFile"][];
            /** Task Id */
            task_id: string;
        };
        /** IntegrationResolution */
        IntegrationResolution: {
            /**
             * Choice
             * @enum {string}
             */
            choice: "current" | "proposed";
            /** Expected Current Sha256 */
            expected_current_sha256: string | null;
            /** Path */
            path: string;
        };
        /** LMStudioConfigRecord */
        LMStudioConfigRecord: {
            /** Endpoint */
            endpoint: string;
            /** Selected Model */
            selected_model: string | null;
        };
        /** LMStudioCredentialRequest */
        LMStudioCredentialRequest: {
            /**
             * Token
             * Format: password
             */
            token: string;
        };
        /** LMStudioCredentialStatus */
        LMStudioCredentialStatus: {
            /** Configured */
            configured: boolean;
        };
        /** LMStudioEndpointRequest */
        LMStudioEndpointRequest: {
            /** Endpoint */
            endpoint: string;
        };
        /** LMStudioSelectionRequest */
        LMStudioSelectionRequest: {
            /** Model */
            model?: string | null;
        };
        /** MCPConnectResponse */
        MCPConnectResponse: {
            /**
             * State
             * @constant
             */
            state: "connected";
            /** Tools */
            tools: components["schemas"]["MCPToolResponse"][];
        };
        /** MCPPromptPreviewRequest */
        MCPPromptPreviewRequest: {
            /** Arguments */
            arguments?: {
                [key: string]: string;
            };
            /** Name */
            name: string;
        };
        /** MCPReconciliationDecision */
        MCPReconciliationDecision: {
            /** Confirmed External Effect Reviewed */
            confirmed_external_effect_reviewed: boolean;
            /**
             * Resolution
             * @enum {string}
             */
            resolution: "applied" | "not_applied";
        };
        /** MCPReconciliationItem */
        MCPReconciliationItem: {
            /** Call Id */
            call_id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "unknown_outcome" | "cancelled_unknown_outcome";
            /** Tool */
            tool: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** MCPResourceReadRequest */
        MCPResourceReadRequest: {
            /** Uri */
            uri: string;
        };
        /** MCPResourceSubscriptionRequest */
        MCPResourceSubscriptionRequest: {
            /** Subscribe */
            subscribe: boolean;
            /** Uri */
            uri: string;
        };
        /** MCPResourceTemplateReadRequest */
        MCPResourceTemplateReadRequest: {
            /** Arguments */
            arguments?: {
                [key: string]: string;
            };
            /** Name */
            name: string;
        };
        /** MCPSecretRequest */
        MCPSecretRequest: {
            /**
             * Value
             * Format: password
             */
            value: string;
        };
        /** MCPServerResponse */
        MCPServerResponse: {
            /** Args */
            args: string[];
            /** Command */
            command: string;
            /** Created At */
            created_at: string;
            /** Cwd */
            cwd: string | null;
            /** Endpoint */
            endpoint: string | null;
            /** Error */
            error: string | null;
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Oauth Configured */
            oauth_configured: boolean;
            /** Plugin Backed */
            plugin_backed: boolean;
            /** Secret Env */
            secret_env: string[];
            /** Secret Env Configured */
            secret_env_configured: {
                [key: string]: boolean;
            };
            /**
             * State
             * @enum {string}
             */
            state: "connected" | "disconnected" | "error";
            /** Tools */
            tools: components["schemas"]["MCPToolResponse"][];
            /**
             * Transport
             * @enum {string}
             */
            transport: "stdio" | "streamable_http";
            /** Updated At */
            updated_at: string;
        };
        /** MCPToolPermissionRequest */
        MCPToolPermissionRequest: {
            /**
             * Permission
             * @enum {string}
             */
            permission: "none" | "read" | "write";
            /** Schema Sha256 */
            schema_sha256: string;
        };
        /** MCPToolResponse */
        MCPToolResponse: {
            /** Description */
            description: string | null;
            /** Input Schema */
            input_schema: {
                [key: string]: unknown;
            };
            /** Name */
            name: string;
            /** Output Schema */
            output_schema: {
                [key: string]: unknown;
            } | null;
            /**
             * Permission
             * @enum {string}
             */
            permission: "none" | "read" | "write";
            /** Qualified Name */
            qualified_name: string;
            /** Schema Sha256 */
            schema_sha256: string;
            /** Server Id */
            server_id: string;
            /** Title */
            title: string | null;
            /** Updated At */
            updated_at: string;
        };
        /** MemoryRecord */
        MemoryRecord: {
            /** Confidence */
            confidence: number | null;
            /** Content */
            content: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            /** Provenance */
            provenance: string;
            /** Schema Version */
            schema_version: number;
            /** Scope */
            scope: string;
            /** Source Uri */
            source_uri?: string | null;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** ModelCapabilities */
        ModelCapabilities: {
            /**
             * Trained For Tool Use
             * @default false
             */
            trained_for_tool_use: boolean;
            /**
             * Vision
             * @default false
             */
            vision: boolean;
        };
        /** ModelInfo */
        ModelInfo: {
            capabilities?: components["schemas"]["ModelCapabilities"];
            /** Context Length */
            context_length?: number | null;
            /** Id */
            id: string;
            /** Loaded */
            loaded?: boolean | null;
            /** Size Bytes */
            size_bytes?: number | null;
        };
        /** ModelRequest */
        ModelRequest: {
            /** Context Length */
            context_length?: number | null;
            /** Model */
            model: string;
        };
        /** PlanRecord */
        PlanRecord: {
            /** Blocked */
            blocked: string[];
            /** Ready */
            ready: string[];
            /** Tasks */
            tasks: components["schemas"]["PlanTaskRecord"][];
        };
        /** PlanTaskRecord */
        PlanTaskRecord: {
            /** Acceptance Criteria */
            acceptance_criteria: string[];
            /** Assigned Role */
            assigned_role: string;
            /** Blocked Reason */
            blocked_reason?: ("token_budget_exhausted_or_unmeasured" | "dependency_failed" | "execution_blocked" | "worker_lost_reconciliation_required" | "prompt_count_mismatch" | "review_evidence_missing") | null;
            budgets: components["schemas"]["TaskBudgets"];
            /** Dependencies */
            dependencies: string[];
            /** Expected Outputs */
            expected_outputs: string[];
            /** Id */
            id: string;
            /** Identity Name */
            identity_name: string;
            /** Input Artifacts */
            input_artifacts: string[];
            /** Local Id */
            local_id: string;
            /** Model Route */
            model_route: string | null;
            /** Objective */
            objective: string;
            /** Permission Scope */
            permission_scope: string[];
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "running" | "verifying" | "completed" | "failed" | "blocked" | "cancelled";
            /** Workspace */
            workspace: string | null;
        };
        /** PluginDataListing */
        PluginDataListing: {
            /** Package Names */
            package_names: string[];
            /** Root */
            root: string;
        };
        /** PluginDataRemovalResult */
        PluginDataRemovalResult: {
            /** Package Name */
            package_name: string;
            /** Removed */
            removed: boolean;
        };
        /** PluginDiagnostic */
        PluginDiagnostic: {
            /** Message */
            message: string;
            /** Path */
            path: string;
        };
        /** PluginDiscovery */
        PluginDiscovery: {
            /** Diagnostics */
            diagnostics: components["schemas"]["PluginDiagnostic"][];
            /** Packages */
            packages: components["schemas"]["PluginSummary"][];
            /** Root */
            root: string;
        };
        /** PluginDiscoveryRequest */
        PluginDiscoveryRequest: {
            /** Root */
            root: string;
        };
        /** PluginInstallRequest */
        PluginInstallRequest: {
            /** Archive Path */
            archive_path: string;
        };
        /** PluginInstallResult */
        PluginInstallResult: {
            package: components["schemas"]["PluginSummary"];
            /** Root */
            root: string;
        };
        /** PluginMCPImportRequest */
        PluginMCPImportRequest: {
            /** Package Name */
            package_name: string;
            /** Root */
            root: string;
            /** Server Name */
            server_name: string;
        };
        /** PluginRemovalRequest */
        PluginRemovalRequest: {
            /** Package Name */
            package_name: string;
        };
        /** PluginRemovalResult */
        PluginRemovalResult: {
            /** Package Name */
            package_name: string;
            /** Removed */
            removed: boolean;
        };
        /** PluginServer */
        PluginServer: {
            /**
             * Argument Count
             * @default 0
             */
            argument_count: number;
            /** Command Name */
            command_name?: string | null;
            /** Endpoint */
            endpoint?: string | null;
            /**
             * Environment Names
             * @default []
             */
            environment_names: string[];
            /** Name */
            name: string;
            /**
             * Transport
             * @enum {string}
             */
            transport: "stdio" | "streamable_http";
        };
        /** PluginSkill */
        PluginSkill: {
            /** Allowed Tools */
            allowed_tools?: string[];
            /** Category */
            category?: string | null;
            /** Declared Requirements */
            declared_requirements?: {
                [key: string]: unknown;
            };
            /** Description */
            description: string;
            /** Name */
            name: string;
            /** Path */
            path: string;
            /** Requirement Readiness */
            requirement_readiness?: {
                [key: string]: unknown;
            };
        };
        /** PluginSkillImportRequest */
        PluginSkillImportRequest: {
            /** Package Name */
            package_name: string;
            /** Root */
            root: string;
            /** Skill Name */
            skill_name: string;
        };
        /** PluginSummary */
        PluginSummary: {
            /** Description */
            description: string | null;
            /** Name */
            name: string;
            /** Path */
            path: string;
            /** Servers */
            servers: components["schemas"]["PluginServer"][];
            /** Skills */
            skills: components["schemas"]["PluginSkill"][];
            /** Version */
            version: string | null;
        };
        /** PreferenceRequest */
        PreferenceRequest: {
            /** Content */
            content: string;
        };
        /** ProjectValidationEvidence */
        ProjectValidationEvidence: {
            /** Argv */
            argv: string[];
            /** Call Id */
            call_id: string;
            /** Exit Code */
            exit_code: number | null;
            /** Matches Current Project */
            matches_current_project: boolean;
            /** Snapshot Sha256 */
            snapshot_sha256: string;
            /** Stderr Artifact Id */
            stderr_artifact_id: string;
            /** Stderr Excerpt */
            stderr_excerpt: string;
            /** Stdout Artifact Id */
            stdout_artifact_id: string;
            /** Stdout Excerpt */
            stdout_excerpt: string;
            /** Succeeded */
            succeeded: boolean;
            /** Termination Reason */
            termination_reason: string;
        };
        /** ProjectValidationRequest */
        ProjectValidationRequest: {
            /** Argv */
            argv: string[];
            /** Expected Revision */
            expected_revision: number;
            /**
             * Max Seconds
             * @default 10
             */
            max_seconds: number;
            /** Request Id */
            request_id: string;
        };
        /** ProjectValidationSuggestion */
        ProjectValidationSuggestion: {
            /** Argv */
            argv: string[];
            /** Label */
            label: string;
            /** Rationale */
            rationale: string;
        };
        /** ProjectValidationSummary */
        ProjectValidationSummary: {
            /** Available */
            available: boolean;
            /** Current Snapshot Sha256 */
            current_snapshot_sha256: string | null;
            /** Evidence */
            evidence: components["schemas"]["ProjectValidationEvidence"][];
            /** Required */
            required: boolean;
            /** Suggestions */
            suggestions?: components["schemas"]["ProjectValidationSuggestion"][];
            /** Unresolved Call Ids */
            unresolved_call_ids?: string[];
        };
        /** PromptCount */
        PromptCount: {
            /** Context Length */
            context_length: number;
            /** Model */
            model: string;
            /** Prompt Tokens */
            prompt_tokens: number;
            /** Sdk Version */
            sdk_version: string;
        };
        /** PromptCountRequest */
        PromptCountRequest: {
            /** Messages */
            messages: components["schemas"]["PromptMessage"][];
            /** Model */
            model: string;
            /** Tools */
            tools?: {
                [key: string]: unknown;
            }[] | null;
        };
        /** PromptMessage */
        PromptMessage: {
            /** Content */
            content?: string | null;
            /**
             * Role
             * @enum {string}
             */
            role: "system" | "user" | "assistant" | "tool";
            /** Tool Call Id */
            tool_call_id?: string | null;
            /** Tool Calls */
            tool_calls?: components["schemas"]["ToolCall"][] | null;
        };
        /** ReadinessRequest */
        ReadinessRequest: {
            /** Distribution */
            distribution: string;
        };
        /** ReadinessResult */
        ReadinessResult: {
            /**
             * Backend
             * @default wsl-bubblewrap-v1
             * @constant
             */
            backend: "wsl-bubblewrap-v1";
            /**
             * Checked At
             * Format: date-time
             */
            checked_at: string;
            /** Checks */
            checks: string[];
            /** Distribution */
            distribution: string;
            /** Error Code */
            error_code?: ("profile_unavailable" | "unsupported_platform" | "backend_probe_failed" | "probe_busy") | null;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            policy?: components["schemas"]["ExecutionPolicy"];
            /** Profile Sha256 */
            profile_sha256: string | null;
            /**
             * State
             * @enum {string}
             */
            state: "ready" | "unavailable" | "unsupported" | "busy";
        };
        /** RevokeChannelIdentityLinkRequest */
        RevokeChannelIdentityLinkRequest: {
            /**
             * Confirmed
             * @constant
             */
            confirmed: true;
        };
        /** RevokeChannelIdentityLinkResponse */
        RevokeChannelIdentityLinkResponse: {
            /** Unlinked Bindings */
            unlinked_bindings: number;
        };
        /** RunEvent */
        RunEvent: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Event Type */
            event_type: string;
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
            /** Sequence */
            sequence: number;
        };
        /** RunHistory */
        RunHistory: {
            /** Items */
            items: components["schemas"]["RunSummary"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** RunLimits */
        RunLimits: {
            /** Max Iterations */
            max_iterations: number;
            /** Max Output Tokens */
            max_output_tokens?: number | null;
            /** Max Seconds */
            max_seconds: number;
            /** Max Total Tokens */
            max_total_tokens?: number | null;
            /**
             * Pre Inference Token Reservations
             * @default false
             */
            pre_inference_token_reservations: boolean;
        };
        /** RunRecord */
        RunRecord: {
            /** Acceptance Criteria */
            acceptance_criteria: string[];
            /**
             * Allow Procedure Drafting
             * @default false
             */
            allow_procedure_drafting: boolean;
            /**
             * Allow Procedure Resource Removal
             * @default false
             */
            allow_procedure_resource_removal: boolean;
            /**
             * Allow Procedure Resource Revision
             * @default false
             */
            allow_procedure_resource_revision: boolean;
            /**
             * Allow Procedure Revision
             * @default false
             */
            allow_procedure_revision: boolean;
            /**
             * Approved Browser
             * @default false
             */
            approved_browser: boolean;
            /** Approved Mcp Tools */
            approved_mcp_tools?: string[];
            /** Approved Web Hosts */
            approved_web_hosts?: string[];
            /**
             * Approved Web Search
             * @default false
             */
            approved_web_search: boolean;
            command_grant?: components["schemas"]["CommandGrant"] | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** File Checks */
            file_checks: components["schemas"]["FileContentCheck"][];
            /** Id */
            id: string;
            limits: components["schemas"]["RunLimits"];
            /**
             * Mode
             * @enum {string}
             */
            mode: "observe" | "workspace" | "authorized_automation";
            /** Model */
            model: string | null;
            /** Objective */
            objective: string;
            procedure_trial?: components["schemas"]["SkillTrialRecord"] | null;
            /** Revision */
            revision: number;
            state: components["schemas"]["RunState"];
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Workspace */
            workspace: string | null;
        };
        /** RunReviewDecision */
        RunReviewDecision: {
            /** Confirmed Criteria */
            confirmed_criteria?: number[];
            /**
             * Decision
             * @enum {string}
             */
            decision: "accept" | "reject";
            /** Expected Revision */
            expected_revision: number;
            /**
             * Objective Confirmed
             * @default false
             */
            objective_confirmed: boolean;
            /** Rationale */
            rationale: string;
            /** Validation Call Ids */
            validation_call_ids?: string[];
        };
        /**
         * RunState
         * @enum {string}
         */
        RunState: "queued" | "planning" | "running" | "verifying" | "waiting_for_approval" | "paused" | "blocked" | "completed" | "failed" | "cancelled";
        /** RunSummary */
        RunSummary: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Id */
            id: string;
            /** Model */
            model: string | null;
            /** Objective */
            objective: string;
            state: components["schemas"]["RunState"];
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Workspace */
            workspace: string | null;
        };
        /** ScheduleDeliveryReconciliationRequest */
        ScheduleDeliveryReconciliationRequest: {
            /**
             * Confirmed Destination Checked
             * @constant
             */
            confirmed_destination_checked: true;
            /**
             * Resolution
             * @enum {string}
             */
            resolution: "delivered" | "retry";
        };
        /** ScheduleEnabledRequest */
        ScheduleEnabledRequest: {
            /** Enabled */
            enabled: boolean;
        };
        /** ScheduleExecutionRecord */
        ScheduleExecutionRecord: {
            /** Completed At */
            completed_at: string | null;
            /** Created At */
            created_at: string;
            /** Delivery Attempt Count */
            delivery_attempt_count: number | null;
            /** Delivery Evidence */
            delivery_evidence: string | null;
            /** Delivery Next Attempt At */
            delivery_next_attempt_at: string | null;
            /** Id */
            id: string;
            /** Occurrence Id */
            occurrence_id: string;
            /** Run Id */
            run_id: string | null;
            /** State */
            state: string;
        };
        /** ScheduleRecord */
        ScheduleRecord: {
            /** Created At */
            created_at: string;
            /** Delivery Destination */
            delivery_destination: string | null;
            /** Enabled */
            enabled: boolean;
            /** Executions */
            executions: components["schemas"]["ScheduleExecutionRecord"][];
            /** Id */
            id: string;
            /** Missed Run Policy */
            missed_run_policy: string;
            /** Objective */
            objective: string;
            /** Overlap Policy */
            overlap_policy: string;
            /** Quiet Hours */
            quiet_hours: string | null;
            /** Recurrence */
            recurrence: {
                [key: string]: unknown;
            };
            /** Timezone */
            timezone: string;
            /** Updated At */
            updated_at: string;
        };
        /** SkillAsset */
        SkillAsset: {
            /** Content Base64 */
            content_base64: string;
            /** Media Type */
            media_type: string;
            /** Path */
            path: string;
            /** Sha256 */
            sha256: string;
        };
        /** SkillDraft */
        SkillDraft: {
            /** Assets */
            assets?: components["schemas"]["SkillAsset"][];
            /**
             * Category
             * @default
             */
            category: string;
            /** Content */
            content: string;
            /**
             * Description
             * @default
             */
            description: string;
            /** Name */
            name: string;
            /** Required Permissions */
            required_permissions: ("workspace.read" | "workspace.write")[];
            /** Required Tools */
            required_tools: components["schemas"]["SkillTool"][];
        };
        /** SkillEvaluation */
        SkillEvaluation: {
            /** Confirmed Criteria */
            confirmed_criteria: number[];
            /** Criteria */
            criteria: string[];
            /** Expected Evaluation Count */
            expected_evaluation_count: number;
            /** Rationale */
            rationale: string;
            /** Trials */
            trials: components["schemas"]["TrialReview"][];
        };
        /** SkillEvidence */
        SkillEvidence: {
            /** Evaluations */
            evaluations: components["schemas"]["EvaluationRecord"][];
            /**
             * Provenance
             * @enum {string}
             */
            provenance: "user" | "model_draft" | "model_revision" | "agent_plugin" | "agent_plugins_v1" | "hermes_skill";
            /** Source */
            source?: {
                [key: string]: string;
            } | null;
            /**
             * State
             * @enum {string}
             */
            state: "candidate" | "evaluated";
        };
        /** SkillLifecycleDecision */
        SkillLifecycleDecision: {
            /**
             * Action
             * @enum {string}
             */
            action: "activate" | "rollback" | "deprecate";
            /** Expected Active Version */
            expected_active_version: number | null;
            /** Expected Latest Version */
            expected_latest_version: number;
            /** Expected Lifecycle Revision */
            expected_lifecycle_revision: number;
            /** Rationale */
            rationale: string;
            /** Target Version */
            target_version?: number | null;
        };
        /** SkillLifecycleEvent */
        SkillLifecycleEvent: {
            /**
             * Action
             * @enum {string}
             */
            action: "activate" | "rollback" | "deprecate";
            /**
             * Actor
             * @constant
             */
            actor: "local-user";
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** From Version */
            from_version: number | null;
            /** Rationale */
            rationale: string;
            /** Sequence */
            sequence: number;
            /** Snapshot Sha256 */
            snapshot_sha256: string | null;
            /** To Version */
            to_version: number | null;
        };
        /** SkillRevision */
        SkillRevision: {
            /** Assets */
            assets?: components["schemas"]["SkillAsset"][];
            /**
             * Category
             * @default
             */
            category: string;
            /** Content */
            content: string;
            /**
             * Description
             * @default
             */
            description: string;
            /** Expected Version */
            expected_version: number;
            /** Name */
            name: string;
            /** Required Permissions */
            required_permissions: ("workspace.read" | "workspace.write")[];
            /** Required Tools */
            required_tools: components["schemas"]["SkillTool"][];
        };
        /** SkillSummary */
        SkillSummary: {
            /** Active Version */
            active_version: number | null;
            /**
             * Category
             * @default
             */
            category: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Description
             * @default
             */
            description: string;
            /** Id */
            id: string;
            /** Latest Version */
            latest_version: number;
            /** Lifecycle Revision */
            lifecycle_revision: number;
            /**
             * Lifecycle State
             * @enum {string}
             */
            lifecycle_state: "candidate" | "evaluated" | "active" | "deprecated";
            /** Name */
            name: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** SkillTool */
        SkillTool: {
            /** Name */
            name: string;
            /** Version */
            version: string;
        };
        /** SkillTrial */
        SkillTrial: {
            /**
             * Purpose
             * @default evaluation_trial
             * @enum {string}
             */
            purpose: "evaluation_trial" | "active_use";
            /** Skill Id */
            skill_id: string;
            /** Version */
            version: number;
        };
        /** SkillTrialRecord */
        SkillTrialRecord: {
            /** Name */
            name: string;
            /**
             * Purpose
             * @default evaluation_trial
             * @enum {string}
             */
            purpose: "evaluation_trial" | "active_use";
            /** Skill Id */
            skill_id: string;
            /** Snapshot Sha256 */
            snapshot_sha256: string;
            /** Version */
            version: number;
        };
        /** SkillTrialRun */
        SkillTrialRun: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Id */
            id: string;
            /** Objective */
            objective: string;
        };
        /** SkillVersion */
        SkillVersion: {
            /** Assets */
            assets?: components["schemas"]["SkillAsset"][];
            /**
             * Category
             * @default
             */
            category: string;
            /** Content */
            content: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Description
             * @default
             */
            description: string;
            evaluation_evidence: components["schemas"]["SkillEvidence"];
            /** Required Permissions */
            required_permissions: string[];
            /** Required Tools */
            required_tools: components["schemas"]["SkillTool"][];
            /** Skill Id */
            skill_id: string;
            /** Version */
            version: number;
        };
        /** SourcedKnowledgeRequest */
        SourcedKnowledgeRequest: {
            /** Content */
            content: string;
            /** Source Uri */
            source_uri: string;
        };
        /** SpecialistIdentityRequest */
        SpecialistIdentityRequest: {
            /** Display Name */
            display_name: string;
        };
        /** SpecialistInstructionsRequest */
        SpecialistInstructionsRequest: {
            /** System Instructions */
            system_instructions: string;
        };
        /** SpecialistProfileResponse */
        SpecialistProfileResponse: {
            /** Display Name */
            display_name: string;
            /** Model Route */
            model_route: string | null;
            /** Role */
            role: string;
            /** System Instructions */
            system_instructions: string;
            /** Updated At */
            updated_at: string;
        };
        /** SpecialistRouteRequest */
        SpecialistRouteRequest: {
            /** Model Route */
            model_route: string | null;
        };
        /** TaskBudgets */
        TaskBudgets: {
            /** Max Iterations */
            max_iterations: number;
            /** Max Output Tokens */
            max_output_tokens?: number | null;
            /** Max Seconds */
            max_seconds: number;
            /** Max Total Tokens */
            max_total_tokens?: number | null;
        };
        /** TaskCandidate */
        TaskCandidate: {
            /** Acceptance Criteria */
            acceptance_criteria: string[];
            /** Candidate */
            candidate: string | null;
            /** Evidence */
            evidence: components["schemas"]["TaskEvidence"][];
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "running" | "verifying" | "completed" | "failed" | "blocked" | "cancelled";
            /** Task Id */
            task_id: string;
            /** Validation Evidence */
            validation_evidence?: components["schemas"]["TaskValidationEvidence"][];
            /**
             * Validation Required
             * @default false
             */
            validation_required: boolean;
            /** Workspace Changes */
            workspace_changes?: components["schemas"]["TaskWorkspaceChange"][];
        };
        /** TaskEvidence */
        TaskEvidence: {
            /** Call Id */
            call_id: string;
            /** Excerpt */
            excerpt: string;
            /** Excerpt Truncated */
            excerpt_truncated: boolean;
            /** Path */
            path: string;
            /** Sha256 */
            sha256: string;
        };
        /** TaskRecoverableAction */
        TaskRecoverableAction: {
            /** Journal Call Id */
            journal_call_id: string;
            /** Mcp Tool */
            mcp_tool?: string | null;
            /** Model Call Id */
            model_call_id: string;
            /**
             * State
             * @enum {string}
             */
            state: "intended" | "completed" | "failed";
            /**
             * Tool Name
             * @enum {string}
             */
            tool_name: "workspace.read_file" | "workspace.list_directory" | "workspace.write_file" | "workspace.create_file" | "workspace.delete_file" | "review_dependency_file" | "review_dependency_output" | "web.fetch" | "web.search" | "browser.run" | "mcp.call";
        };
        /** TaskRecoverableCommand */
        TaskRecoverableCommand: {
            /** Argv */
            argv: string[];
            /** Journal Call Id */
            journal_call_id: string;
            /** Model Call Id */
            model_call_id: string;
            /**
             * State
             * @enum {string}
             */
            state: "intended" | "completed" | "failed";
        };
        /** TaskRecoveryDecision */
        TaskRecoveryDecision: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "resume" | "review_candidate" | "fail";
        };
        /** TaskRecoverySummary */
        TaskRecoverySummary: {
            /** Candidate */
            candidate: string | null;
            /** Candidate Reviewable */
            candidate_reviewable: boolean;
            /** Consumed Iterations */
            consumed_iterations: number;
            /** Pending Model Tool Calls */
            pending_model_tool_calls: {
                [key: string]: string | null;
            }[];
            /** Recoverable Commands */
            recoverable_commands: components["schemas"]["TaskRecoverableCommand"][];
            /** Recoverable Task Actions */
            recoverable_task_actions: components["schemas"]["TaskRecoverableAction"][];
            /** Reserved Tokens */
            reserved_tokens: number;
            /** Resume From Start */
            resume_from_start: boolean;
            /** Run Measured Tokens */
            run_measured_tokens: number;
            /** Safe To Resume */
            safe_to_resume: boolean;
            /** Saved Tool Results */
            saved_tool_results: number;
            /**
             * Status
             * @constant
             */
            status: "blocked";
            /** Task Id */
            task_id: string;
            /** Unresolved Tool Calls */
            unresolved_tool_calls: number;
        };
        /** TaskReviewDecision */
        TaskReviewDecision: {
            /**
             * Changes Confirmed
             * @default false
             */
            changes_confirmed: boolean;
            /** Confirmed Criteria */
            confirmed_criteria?: number[];
            /**
             * Decision
             * @enum {string}
             */
            decision: "accept" | "reject";
            /** Evidence Call Ids */
            evidence_call_ids?: string[];
            /** Rationale */
            rationale: string;
            /** Resolutions */
            resolutions?: components["schemas"]["IntegrationResolution"][];
            /** Validation Call Ids */
            validation_call_ids?: string[];
        };
        /** TaskValidationEvidence */
        TaskValidationEvidence: {
            /** Argv */
            argv: string[];
            /** Call Id */
            call_id: string;
            /** Exit Code */
            exit_code: number | null;
            /** Matches Current Private Tree */
            matches_current_private_tree: boolean;
            /** Snapshot Sha256 */
            snapshot_sha256: string;
            /** Stderr Artifact Id */
            stderr_artifact_id: string;
            /** Stderr Excerpt */
            stderr_excerpt: string;
            /** Stdout Artifact Id */
            stdout_artifact_id: string;
            /** Stdout Excerpt */
            stdout_excerpt: string;
            /** Succeeded */
            succeeded: boolean;
            /** Termination Reason */
            termination_reason: string;
        };
        /** TaskWorkspaceChange */
        TaskWorkspaceChange: {
            /** Base Sha256 */
            base_sha256: string | null;
            /** Diff */
            diff: string;
            /** Diff Truncated */
            diff_truncated: boolean;
            /** Path */
            path: string;
            /** Proposed Sha256 */
            proposed_sha256: string | null;
        };
        /** TelegramCredentialRequest */
        TelegramCredentialRequest: {
            /**
             * Token
             * Format: password
             */
            token: string;
        };
        /** TelegramCredentialStatus */
        TelegramCredentialStatus: {
            /** Bot Username */
            bot_username?: string | null;
            /** Configured */
            configured: boolean;
            /** Deliveries */
            deliveries: {
                [key: string]: number;
            };
            /** Linked Users */
            linked_users: number;
            /** Processed Updates */
            processed_updates: number;
            /** Unknown Deliveries */
            unknown_deliveries: components["schemas"]["TelegramUnknownDelivery"][];
        };
        /** TelegramDeliveryReconciliation */
        TelegramDeliveryReconciliation: {
            /**
             * Confirmed Destination Checked
             * @constant
             */
            confirmed_destination_checked: true;
            /**
             * Resolution
             * @enum {string}
             */
            resolution: "delivered" | "retry";
        };
        /** TelegramLinkRevocation */
        TelegramLinkRevocation: {
            /** Linked Users */
            linked_users: number;
            /** Revoked Users */
            revoked_users: number;
        };
        /** TelegramLinkedUser */
        TelegramLinkedUser: {
            /** User Id */
            user_id: string;
        };
        /** TelegramPairingCode */
        TelegramPairingCode: {
            /** Code */
            code: string;
            /** Expires At */
            expires_at: string;
        };
        /** TelegramUnknownDelivery */
        TelegramUnknownDelivery: {
            /** Run Id */
            run_id: string | null;
            /** Update Id */
            update_id: number;
            /** Updated At */
            updated_at: string;
            /** User Id */
            user_id: string | null;
        };
        /** ToolCall */
        ToolCall: {
            function: components["schemas"]["FunctionCall"];
            /** Id */
            id: string;
            /**
             * Type
             * @constant
             */
            type: "function";
        };
        /** TrialReview */
        TrialReview: {
            /** Case Description */
            case_description: string;
            /** Run Id */
            run_id: string;
        };
        /** UnloadRequest */
        UnloadRequest: {
            /** Instance Id */
            instance_id: string;
        };
        /** UsageReport */
        UsageReport: {
            /** Completion Tokens */
            completion_tokens: number;
            /** Outstanding Reservations */
            outstanding_reservations: number;
            /** Outstanding Reserved Tokens */
            outstanding_reserved_tokens: number;
            /** Prompt Tokens */
            prompt_tokens: number;
            /** Total Tokens */
            total_tokens: number;
            /** Unmeasured Calls */
            unmeasured_calls: number;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** WebSearchCredentialRequest */
        WebSearchCredentialRequest: {
            /**
             * Token
             * Format: password
             */
            token: string;
        };
        /** WebhookChannelStatus */
        WebhookChannelStatus: {
            /** Configured */
            configured: boolean;
        };
        /** WebhookCredentialRecord */
        WebhookCredentialRecord: {
            /** Configured */
            configured: boolean;
            /** Secret */
            secret: string;
        };
        /** WebhookReceipt */
        WebhookReceipt: {
            /** Run Id */
            run_id: string;
            state: components["schemas"]["RunState"];
        };
        /** WorkspaceWriteReconciliationDecision */
        WorkspaceWriteReconciliationDecision: {
            /** Confirmed Workspace Inspected */
            confirmed_workspace_inspected: boolean;
        };
        /** WorkspaceWriteReconciliationItem */
        WorkspaceWriteReconciliationItem: {
            /** Call Id */
            call_id: string;
            /** Desired Sha256 */
            desired_sha256: string;
            /** Expected Sha256 */
            expected_sha256: string | null;
            /**
             * Operation
             * @enum {string}
             */
            operation: "create" | "edit" | "delete";
            /** Path */
            path: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    download_artifact_artifacts__artifact_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                artifact_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_backup_backups_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    discord_status_channels_discord_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiscordCredentialStatus"];
                };
            };
        };
    };
    set_discord_credential_channels_discord_credential_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DiscordCredentialRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiscordCredentialStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    clear_discord_credential_channels_discord_credential_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiscordCredentialStatus"];
                };
            };
        };
    };
    reconcile_discord_delivery_channels_discord_deliveries__message_id__reconcile_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                message_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DiscordDeliveryReconciliation"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiscordCredentialStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    revoke_discord_links_channels_discord_links_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiscordLinkRevocation"];
                };
            };
        };
    };
    create_discord_pairing_channels_discord_pairing_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiscordPairingCode"];
                };
            };
        };
    };
    email_status_channels_email_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EmailCredentialStatus"];
                };
            };
        };
    };
    set_email_credential_channels_email_credential_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EmailCredentialRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EmailCredentialStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    clear_email_credential_channels_email_credential_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EmailCredentialStatus"];
                };
            };
        };
    };
    reconcile_email_delivery_channels_email_deliveries__uid_validity___uid__reconcile_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                uid_validity: string;
                uid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EmailDeliveryReconciliation"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EmailCredentialStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_channel_identity_bindings_channels_identity_bindings_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChannelIdentityBinding"][];
                };
            };
        };
    };
    create_channel_identity_link_channels_identity_links_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChannelIdentityLinkRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChannelIdentityLinkRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    revoke_channel_identity_link_channels_identity_links__link_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                link_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RevokeChannelIdentityLinkRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RevokeChannelIdentityLinkResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_channel_identity_link_member_channels_identity_links__link_id__members_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                link_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChannelIdentityLinkMemberRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChannelIdentityLinkMemberResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    telegram_status_channels_telegram_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TelegramCredentialStatus"];
                };
            };
        };
    };
    set_telegram_credential_channels_telegram_credential_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TelegramCredentialRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TelegramCredentialStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    clear_telegram_credential_channels_telegram_credential_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TelegramCredentialStatus"];
                };
            };
        };
    };
    reconcile_telegram_delivery_channels_telegram_deliveries__update_id__reconcile_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                update_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TelegramDeliveryReconciliation"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TelegramCredentialStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_telegram_linked_users_channels_telegram_linked_users_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TelegramLinkedUser"][];
                };
            };
        };
    };
    revoke_telegram_links_channels_telegram_links_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TelegramLinkRevocation"];
                };
            };
        };
    };
    create_telegram_pairing_channels_telegram_pairing_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TelegramPairingCode"];
                };
            };
        };
    };
    webhook_status_channels_webhook_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WebhookChannelStatus"];
                };
            };
        };
    };
    create_webhook_credential_channels_webhook_credential_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WebhookCredentialRecord"];
                };
            };
        };
    };
    delete_webhook_credential_channels_webhook_credential_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WebhookChannelStatus"];
                };
            };
        };
    };
    list_acp_chat_sessions_chat_acp_sessions_get: {
        parameters: {
            query: {
                model: string;
                workspace: string;
                mode: "observe" | "workspace";
                after?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_acp_chat_session_chat_acp_sessions__session_id__delete: {
        parameters: {
            query: {
                model: string;
                workspace: string;
                mode: "observe" | "workspace";
            };
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_cli_chat_sessions_chat_sessions_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CLIChatSessionSummary"][];
                };
            };
        };
    };
    get_cli_chat_session_chat_sessions__session_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CLIChatSessionRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ensure_cli_chat_session_chat_sessions__session_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CLIChatSessionSettings"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CLIChatSessionRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_cli_chat_session_chat_sessions__session_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    append_cli_chat_turn_chat_sessions__session_id__turn_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CLIChatTurnRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CLIChatSessionRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stream_chat_chat_stream_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChatRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    execution_distributions_execution_distributions_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DistributionInventory"];
                };
            };
        };
    };
    check_execution_readiness_execution_readiness_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReadinessRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadinessResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_extension_package_data_extensions_packages_data_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PluginDataListing"];
                };
            };
        };
    };
    remove_extension_package_data_extensions_packages_data_remove_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PluginRemovalRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PluginDataRemovalResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    discover_extension_packages_extensions_packages_discover_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PluginDiscoveryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PluginDiscovery"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    import_extension_mcp_server_extensions_packages_import_mcp_server_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PluginMCPImportRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    import_extension_skill_extensions_packages_import_skill_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PluginSkillImportRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    install_extension_package_extensions_packages_install_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PluginInstallRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PluginInstallResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    discover_installed_extension_packages_extensions_packages_installed_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PluginDiscovery"];
                };
            };
        };
    };
    remove_extension_package_extensions_packages_remove_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PluginRemovalRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PluginRemovalResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    list_mcp_servers_mcp_servers_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MCPServerResponse"][];
                };
            };
        };
    };
    create_mcp_server_mcp_servers_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateMCPServerRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MCPServerResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_mcp_server_mcp_servers__server_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    connect_mcp_server_mcp_servers__server_id__connect_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MCPConnectResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    disconnect_mcp_server_mcp_servers__server_id__disconnect_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_mcp_oauth_mcp_servers__server_id__oauth_delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_mcp_prompts_mcp_servers__server_id__prompts_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_mcp_prompt_mcp_servers__server_id__prompts_preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MCPPromptPreviewRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_mcp_resource_templates_mcp_servers__server_id__resource_templates_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    read_mcp_resource_template_mcp_servers__server_id__resource_templates_read_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MCPResourceTemplateReadRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_mcp_resources_mcp_servers__server_id__resources_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    drain_mcp_resource_notifications_mcp_servers__server_id__resources_notifications_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    read_mcp_resource_mcp_servers__server_id__resources_read_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MCPResourceReadRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_mcp_resource_subscription_mcp_servers__server_id__resources_subscription_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MCPResourceSubscriptionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_mcp_secret_mcp_servers__server_id__secrets__env_name__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
                env_name: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MCPSecretRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: boolean;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_mcp_secret_mcp_servers__server_id__secrets__env_name__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
                env_name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_mcp_tool_permission_mcp_servers__server_id__tools__tool_name__permission_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                server_id: string;
                tool_name: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MCPToolPermissionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MCPToolResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    search_memory_memory_get: {
        parameters: {
            query?: {
                q?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryRecord"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_memory_memory_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PreferenceRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_memory_memory_export_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryRecord"][];
                };
            };
        };
    };
    create_sourced_knowledge_memory_knowledge_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SourcedKnowledgeRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    edit_sourced_knowledge_memory_knowledge__memory_id__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                memory_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SourcedKnowledgeRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    edit_memory_memory__memory_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                memory_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PreferenceRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_memory_memory__memory_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                memory_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    models_models_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelInfo"][];
                };
            };
        };
    };
    load_model_models_load_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ModelRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    unload_model_models_unload_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UnloadRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    providers_providers_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
        };
    };
    brave_search_credential_status_providers_brave_search_credential_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioCredentialStatus"];
                };
            };
        };
    };
    set_brave_search_credential_providers_brave_search_credential_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WebSearchCredentialRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioCredentialStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_brave_search_credential_providers_brave_search_credential_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioCredentialStatus"];
                };
            };
        };
    };
    diagnose_model_providers_diagnostics_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ModelRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    lm_studio_config_providers_lm_studio_config_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioConfigRecord"];
                };
            };
        };
    };
    set_lm_studio_endpoint_providers_lm_studio_config_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LMStudioEndpointRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioConfigRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    lm_studio_credential_status_providers_lm_studio_credential_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioCredentialStatus"];
                };
            };
        };
    };
    set_lm_studio_credential_providers_lm_studio_credential_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LMStudioCredentialRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioCredentialStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_lm_studio_credential_providers_lm_studio_credential_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioCredentialStatus"];
                };
            };
        };
    };
    set_lm_studio_model_providers_lm_studio_selection_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LMStudioSelectionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string | null;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    compatible_provider_config_providers_openai_compatible_config_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CompatibleProviderConfigRecord"];
                };
            };
        };
    };
    set_compatible_provider_config_providers_openai_compatible_config_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CompatibleProviderConfigRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CompatibleProviderConfigRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    compatible_provider_credential_status_providers_openai_compatible_credential_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioCredentialStatus"];
                };
            };
        };
    };
    set_compatible_provider_credential_providers_openai_compatible_credential_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LMStudioCredentialRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioCredentialStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_compatible_provider_credential_providers_openai_compatible_credential_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LMStudioCredentialStatus"];
                };
            };
        };
    };
    set_compatible_provider_model_providers_openai_compatible_selection_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CompatibleProviderSelectionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string | null;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    token_count_providers_token_count_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PromptCountRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PromptCount"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    run_history_runs_get: {
        parameters: {
            query?: {
                limit?: number;
                before?: string | null;
                q?: string;
                scope?: "objective" | "conversation";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunHistory"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_run_runs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateRunRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_latest_unfinished_run_runs_latest_unfinished_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunSummary"] | null;
                };
            };
        };
    };
    get_run_runs__run_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    artifacts_runs__run_id__artifacts_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactRecord"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_run_runs__run_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_events_runs__run_id__events_get: {
        parameters: {
            query?: {
                after?: number;
            };
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunEvent"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stream_run_events_runs__run_id__events_stream_get: {
        parameters: {
            query?: {
                after?: number;
            };
            header?: {
                "last-event-id"?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_run_trajectory_runs__run_id__export_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_mcp_reconciliations_runs__run_id__mcp_reconciliations_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MCPReconciliationItem"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pause_run_runs__run_id__pause_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_plan_runs__run_id__plan_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlanRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    dispatch_plan_runs__run_id__plan_dispatch_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resume_run_runs__run_id__resume_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decide_run_runs__run_id__review_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RunReviewDecision"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_candidate_runs__run_id__tasks__task_id__candidate_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                task_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskCandidate"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_task_integration_runs__run_id__tasks__task_id__integration_preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                task_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IntegrationPreview"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_task_recovery_runs__run_id__tasks__task_id__recovery_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                task_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskRecoverySummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decide_task_recovery_runs__run_id__tasks__task_id__recovery_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                task_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TaskRecoveryDecision"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlanRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    attach_saved_task_action_runs__run_id__tasks__task_id__recovery_actions__model_call_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                task_id: string;
                model_call_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskRecoverySummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reconcile_task_command_runs__run_id__tasks__task_id__recovery_commands__model_call_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                task_id: string;
                model_call_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskRecoverySummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decide_candidate_runs__run_id__tasks__task_id__review_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                task_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TaskReviewDecision"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlanRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reconcile_mcp_effect_runs__run_id__tools__call_id__mcp_reconcile_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                call_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MCPReconciliationDecision"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MCPReconciliationItem"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_public_tool_result_runs__run_id__tools__call_id__result_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                call_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string | null;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reconcile_workspace_write_not_applied_runs__run_id__tools__call_id__workspace_write_reconcile_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                call_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceWriteReconciliationDecision"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkspaceWriteReconciliationItem"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_usage_runs__run_id__usage_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UsageReport"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_project_validation_runs__run_id__validation_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProjectValidationSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    run_project_validation_runs__run_id__validation_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProjectValidationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProjectValidationSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reconcile_project_validation_runs__run_id__validation__call_id__reconcile_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                call_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProjectValidationSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_workspace_write_reconciliations_runs__run_id__workspace_write_reconciliations_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkspaceWriteReconciliationItem"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    schedules_schedules_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleRecord"][];
                };
            };
        };
    };
    create_schedule_schedules_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateScheduleRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_schedule_enabled_schedules__schedule_id__enabled_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                schedule_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScheduleEnabledRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reconcile_schedule_delivery_schedules__schedule_id__executions__execution_id__delivery_reconcile_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                schedule_id: string;
                execution_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScheduleDeliveryReconciliationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    shutdown_shutdown_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    skills_skills_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillSummary"][];
                };
            };
        };
    };
    create_skill_skills_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SkillDraft"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_skill_skills__skill_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                skill_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_skill_skills__skill_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                skill_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    skill_lifecycle_skills__skill_id__lifecycle_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                skill_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillLifecycleEvent"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decide_skill_lifecycle_skills__skill_id__lifecycle_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                skill_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SkillLifecycleDecision"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    revise_skill_skills__skill_id__versions_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                skill_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SkillRevision"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    skill_version_skills__skill_id__versions__version__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                skill_id: string;
                version: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillVersion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    evaluate_skill_skills__skill_id__versions__version__evaluations_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                skill_id: string;
                version: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SkillEvaluation"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillVersion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_skill_package_skills__skill_id__versions__version__export_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                skill_id: string;
                version: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/zip": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    eligible_skill_trials_skills__skill_id__versions__version__trials_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                skill_id: string;
                version: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillTrialRun"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_specialist_profiles_specialists_profiles_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SpecialistProfileResponse"][];
                };
            };
        };
    };
    set_specialist_identity_specialists_profiles__role__identity_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                role: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SpecialistIdentityRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SpecialistProfileResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_specialist_instructions_specialists_profiles__role__instructions_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                role: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SpecialistInstructionsRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SpecialistProfileResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_specialist_memory_specialists_profiles__role__memory_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                role: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryRecord"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_specialist_memory_specialists_profiles__role__memory_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                role: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PreferenceRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryRecord"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_specialist_memory_specialists_profiles__role__memory__memory_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                role: string;
                memory_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_specialist_route_specialists_profiles__role__route_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                role: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SpecialistRouteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SpecialistProfileResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    receive_webhook_webhooks_inbound_post: {
        parameters: {
            query?: never;
            header?: {
                "X-XAA-Timestamp"?: string | null;
                "X-XAA-Event-Id"?: string | null;
                "X-XAA-Signature"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WebhookReceipt"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
