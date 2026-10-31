import { useCallback, useEffect, useState } from "react";

import { Backdrop } from "../components/Art";
import { MixedHeading, PageHeader } from "../components/Controls";
import { AlertIcon, CheckIcon, CopyIcon } from "../components/Icons";
import { copy } from "../i18n/zh-CN";
import {
  type AgentProvider,
  api,
  type ApiSettings,
  type Readiness,
  type SpeechModel
} from "../lib/api";
import { errorMessage } from "../lib/format";
import { cn } from "../lib/utils";
import { useReadiness } from "./Home";

const SERVICE_ORDER = [
  "agent",
  "modal",
  "cloudflare",
  "private_feed",
  "youtube",
  "telegram",
  "listening_audit",
  "source_channel"
];

const AGENTS: Array<{ value: AgentProvider; label: string }> = [
  { value: "codex", label: "Codex" },
  { value: "claude", label: "Claude Code" }
];

const VOICES: Array<{ value: SpeechModel; label: string }> = [
  { value: "indextts2", label: "IndexTTS 2.0" },
  { value: "indextts25", label: "IndexTTS 2.5" }
];

function serviceNote(key: string, readiness: Readiness): string {
  if (key === "agent" && readiness.agent) {
    const provider = readiness.agent.providers[readiness.agent.provider];
    const name = AGENTS.find((item) => item.value === readiness.agent?.provider)?.label;
    if (provider) {
      return [name, copy.settings.agentAuth(provider.auth_method)]
        .filter(Boolean)
        .join(" · ");
    }
  }
  return copy.settings.serviceNotes[key] ?? "";
}

export function SettingsPage() {
  const { readiness, checking, recheck } = useReadiness();
  const [settings, setSettings] = useState<ApiSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback((signal?: AbortSignal) => {
    api
      .settings(signal)
      .then(setSettings)
      .catch((reason: unknown) => {
        if (!signal?.aborted) {
          setNotice(errorMessage(reason));
        }
      });
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const save = (update: Parameters<typeof api.updateSettings>[0]) => {
    setSaving(true);
    setNotice("");
    api
      .updateSettings(update)
      .then(setSettings)
      .catch((reason: unknown) =>
        setNotice(`${copy.settings.saveFailed}：${errorMessage(reason)}`)
      )
      .finally(() => setSaving(false));
  };

  const copyFeed = () => {
    api
      .feedSubscription()
      .then((value) => navigator.clipboard.writeText(value.subscription_url))
      .then(() => setNotice(copy.actions.feedCopied))
      .catch(() => setNotice(copy.actions.feedCopyFailed));
  };
  const openApple = () => {
    api
      .feedSubscription()
      .then((value) => {
        window.location.href = value.apple_podcasts_url;
      })
      .catch((reason: unknown) => setNotice(errorMessage(reason)));
  };

  const services = readiness
    ? SERVICE_ORDER.filter(
        (key) =>
          key in readiness.services &&
          (key !== "source_channel" || !readiness.services[key])
      )
    : [];

  return (
    <div className="page detail-page settings-page">
      <Backdrop className="settings-backdrop" />
      <PageHeader title={copy.settings.title} />
      <div className="settings-grid">
        <div className="settings-column">
          <section className="feed-section" aria-labelledby="feed-title">
            <MixedHeading
              bold={copy.settings.feedBold}
              light={copy.settings.feedLight}
              id="feed-title"
              className="display-heading"
            />
            <p className="muted-paragraph">{copy.settings.feedNote}</p>
            <div className="feed-field">
              <code>
                {settings?.publication.subscription_url_masked ?? "…"}
              </code>
              <button
                type="button"
                className="circle-button"
                aria-label={copy.actions.copyFeed}
                onClick={copyFeed}
              >
                <CopyIcon size={17} />
              </button>
            </div>
            <button
              type="button"
              className="pill-button is-primary is-block"
              onClick={openApple}
            >
              {copy.settings.openApple}
            </button>
          </section>

          <section className="panel" aria-labelledby="processing-title">
            <div>
              <MixedHeading
                bold={copy.settings.processingBold}
                light={copy.settings.processingLight}
                id="processing-title"
                className="panel-subtitle"
              />
              <p className="muted-note">{copy.settings.processingNote}</p>
            </div>
            {settings ? (
              <>
                <ChoiceGroup
                  legend={copy.settings.agent}
                  options={AGENTS}
                  value={settings.agent?.provider}
                  disabled={saving}
                  onChange={(value) => save({ agent_provider: value })}
                />
                <ChoiceGroup
                  legend={copy.settings.voice}
                  options={VOICES}
                  value={settings.speech.model}
                  disabled={saving}
                  onChange={(value) => save({ speech_model: value })}
                />
              </>
            ) : (
              <p className="muted-paragraph">{copy.settings.loading}</p>
            )}
          </section>
          {notice ? (
            <p className="panel-notice" role="status">
              {notice}
            </p>
          ) : null}
        </div>

        <section className="panel status-panel" aria-labelledby="status-title">
          <div className="status-head">
            <div>
              <MixedHeading
                bold={copy.settings.statusBold}
                light={copy.settings.statusLight}
                id="status-title"
                className="display-heading"
              />
              <p className="muted-paragraph">
                {readiness
                  ? readiness.ready
                    ? copy.settings.allReady
                    : copy.settings.notReady
                  : copy.settings.loading}
              </p>
            </div>
            <button
              type="button"
              className="pill-button is-outline is-small"
              disabled={checking}
              onClick={recheck}
            >
              {checking ? copy.settings.rechecking : copy.settings.recheck}
            </button>
          </div>
          <ul className="service-list">
            {readiness
              ? services.map((key) => {
                  const ok = readiness.services[key];
                  const optional = readiness.details[key]?.optional;
                  return (
                    <li key={key} className={cn(!ok && "is-missing")}>
                      <span className="check-badge" aria-hidden="true">
                        {ok ? <CheckIcon size={15} /> : <AlertIcon size={16} />}
                      </span>
                      <span className="service-text">
                        <span className="service-name">
                          {copy.settings.services[key] ?? key}
                          {!ok ? (
                            <span className="hard-tag">
                              {optional ? copy.settings.optional : copy.settings.missing}
                            </span>
                          ) : null}
                        </span>
                        <span className="muted-note">
                          {serviceNote(key, readiness)}
                        </span>
                      </span>
                    </li>
                  );
                })
              : null}
          </ul>
        </section>
      </div>
    </div>
  );
}

function ChoiceGroup<T extends string>({
  legend,
  options,
  value,
  disabled,
  onChange
}: {
  legend: string;
  options: Array<{ value: T; label: string }>;
  value: T | undefined;
  disabled: boolean;
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="choice-group" disabled={disabled}>
      <legend>{legend}</legend>
      <div className="chip-group">
        {options.map((option) => (
          <label
            key={option.value}
            className={cn("chip", option.value === value && "is-selected")}
          >
            <input
              type="radio"
              className="visually-hidden"
              name={legend}
              checked={option.value === value}
              onChange={() => onChange(option.value)}
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
