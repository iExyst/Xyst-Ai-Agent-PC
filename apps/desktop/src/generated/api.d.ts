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
    "/providers/lm-studio/diagnostics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Diagnose Model */
        post: operations["diagnose_model_providers_lm_studio_diagnostics_post"];
        delete?: never;
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
    "/providers/lm-studio/token-count": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Token Count */
        post: operations["token_count_providers_lm_studio_token_count_post"];
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
        /** CreateRunRequest */
        CreateRunRequest: {
            /** Acceptance Criteria */
            acceptance_criteria?: string[];
            command_grant?: components["schemas"]["CommandGrant"] | null;
            /** File Checks */
            file_checks?: components["schemas"]["FileContentCheck"][];
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
            procedure_trial?: components["schemas"]["SkillTrial"] | null;
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
            proposed_sha256: string;
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
            blocked_reason?: ("token_budget_exhausted_or_unmeasured" | "dependency_failed" | "execution_blocked") | null;
            budgets: components["schemas"]["TaskBudgets"];
            /** Dependencies */
            dependencies: string[];
            /** Id */
            id: string;
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
        };
        /** RunRecord */
        RunRecord: {
            /** Acceptance Criteria */
            acceptance_criteria: string[];
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
        /** SkillDraft */
        SkillDraft: {
            /** Content */
            content: string;
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
             * @constant
             */
            provenance: "user";
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
            /** Content */
            content: string;
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
             * Created At
             * Format: date-time
             */
            created_at: string;
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
            /** Content */
            content: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
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
            proposed_sha256: string;
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
    diagnose_model_providers_lm_studio_diagnostics_post: {
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
    token_count_providers_lm_studio_token_count_post: {
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
}
