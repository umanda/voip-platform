<!--
  conf/vars.xml.tpl — Template for FreeSWITCH global variables.

  DO NOT EDIT vars.xml directly — it is generated from this template by
  docker-entrypoint.sh at container start via envsubst.

  Substitution variables (${VAR}) are replaced with the corresponding
  environment variables. FreeSWITCH's own runtime references ($${var})
  in other conf files are NOT affected — envsubst only touches the listed
  shell variable names.

  Hot reload after regenerating vars.xml (no FS restart needed):
    fs_cli -x "reloadxml"

  Migration Notes:
    Legacy: sofia.conf INI file read via Config::Tiny (getConfValue in utils.pm)
    New:    FreeSWITCH global vars — hot-reloadable without FS restart.
-->
<include>

  <!-- FastAPI endpoint — ECS ALB DNS in production; service name in Docker dev -->
  <X-PRE-PROCESS cmd="set" data="voip_api_base_url=${VOIP_API_BASE_URL}"/>

  <!-- Internal service token — injected from AWS Secrets Manager at runtime -->
  <!-- Never commit a real token here or in vars.xml                          -->
  <X-PRE-PROCESS cmd="set" data="voip_internal_token=${VOIP_INTERNAL_TOKEN}"/>

  <!-- Sound file paths — match FreeSWITCH install layout -->
  <X-PRE-PROCESS cmd="set" data="voip_sounds_base=${VOIP_SOUNDS_BASE}"/>
  <X-PRE-PROCESS cmd="set" data="voip_digits_base=${VOIP_DIGITS_BASE}"/>

  <!-- IVR tuning — matches legacy sofia.conf [auth] and [dial_coach] -->
  <X-PRE-PROCESS cmd="set" data="voip_auth_attempts=${VOIP_AUTH_ATTEMPTS}"/>
  <X-PRE-PROCESS cmd="set" data="voip_pin_length=${VOIP_PIN_LENGTH}"/>
  <X-PRE-PROCESS cmd="set" data="voip_dtmf_timeout_ms=${VOIP_DTMF_TIMEOUT_MS}"/>

  <!-- HTTP timeouts (milliseconds) — R-SIP-01: auth ≤ 2000ms -->
  <X-PRE-PROCESS cmd="set" data="voip_http_timeout_ms=${VOIP_HTTP_TIMEOUT_MS}"/>
  <X-PRE-PROCESS cmd="set" data="voip_tick_timeout_ms=${VOIP_TICK_TIMEOUT_MS}"/>

  <!-- Credit warning thresholds — matches sofia.conf warn/beep time limits -->
  <X-PRE-PROCESS cmd="set" data="voip_warn_remaining_seconds=${VOIP_WARN_REMAINING_SEC}"/>
  <X-PRE-PROCESS cmd="set" data="voip_beep_remaining_seconds=${VOIP_BEEP_REMAINING_SEC}"/>

  <!-- Default gateway — matches sofia.conf [providers] provider1 -->
  <X-PRE-PROCESS cmd="set" data="voip_default_gateway=${VOIP_DEFAULT_GATEWAY}"/>

  <!-- Voxbone SIP trunk credentials — injected from AWS Secrets Manager -->
  <X-PRE-PROCESS cmd="set" data="voxbone_username=${VOXBONE_USERNAME}"/>
  <X-PRE-PROCESS cmd="set" data="voxbone_password=${VOXBONE_PASSWORD}"/>
  <X-PRE-PROCESS cmd="set" data="voxbone_realm=${VOXBONE_REALM}"/>
  <X-PRE-PROCESS cmd="set" data="voxbone_proxy=${VOXBONE_PROXY}"/>

  <!-- Codec preferences — R-MEDIA-01: G.711 first -->
  <X-PRE-PROCESS cmd="set" data="global_codec_prefs=PCMU,PCMA,G729,OPUS"/>
  <X-PRE-PROCESS cmd="set" data="outbound_codec_prefs=PCMU,PCMA,G729"/>

  <!-- RTP port range — R-INFRA-02: must be open bidirectionally in Security Group -->
  <X-PRE-PROCESS cmd="set" data="rtp_start_port=${RTP_START_PORT}"/>
  <X-PRE-PROCESS cmd="set" data="rtp_end_port=${RTP_END_PORT}"/>

</include>
