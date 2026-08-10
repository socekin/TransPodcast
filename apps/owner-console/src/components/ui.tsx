import { Button as BaseButton } from "@base-ui/react/button";
import { Check } from "lucide-react";
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  PropsWithChildren,
  ReactNode
} from "react";

import { cn } from "../lib/utils";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
};

export function Button({
  className,
  tone = "primary",
  size = "md",
  ...props
}: ButtonProps) {
  return (
    <BaseButton
      className={cn("button", `button-${tone}`, `button-${size}`, className)}
      {...props}
    />
  );
}

export function IconButton({
  label,
  children,
  className,
  ...props
}: PropsWithChildren<
  ButtonHTMLAttributes<HTMLButtonElement> & { label: string }
>) {
  return (
    <BaseButton
      aria-label={label}
      className={cn("icon-button", className)}
      {...props}
    >
      {children}
    </BaseButton>
  );
}

export function Card({
  children,
  className,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLElement>>) {
  return (
    <section className={cn("card", className)} {...props}>
      {children}
    </section>
  );
}

export function AsyncResourceState({
  kind,
  icon,
  title,
  detail,
  onRetry,
  retryLabel,
  className,
  surface = "plain"
}: {
  kind: "loading" | "error" | "empty";
  icon?: ReactNode;
  title: string;
  detail?: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
  surface?: "plain" | "card";
}) {
  const role =
    kind === "loading"
      ? "status"
      : kind === "error"
        ? "alert"
        : undefined;
  const children = (
    <>
      {icon}
      <strong>{title}</strong>
      {detail ? <span>{detail}</span> : null}
      {onRetry && retryLabel ? (
        <Button size="sm" onClick={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </>
  );
  return surface === "card" ? (
    <Card className={className} role={role}>
      {children}
    </Card>
  ) : (
    <div className={className} role={role}>
      {children}
    </div>
  );
}

export function Badge({
  children,
  tone = "neutral"
}: PropsWithChildren<{
  tone?: "neutral" | "primary" | "success" | "warning" | "danger";
}>) {
  return <span className={cn("badge", `badge-${tone}`)}>{children}</span>;
}

export function Field({
  label,
  hint,
  children
}: PropsWithChildren<{ label: string; hint?: string }>) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {hint ? <span className="field-hint">{hint}</span> : null}
      {children}
    </label>
  );
}

export function ServiceStatus({
  title,
  note,
  configured = true,
  optional = false,
  readyLabel = "Ready",
  missingLabel = "Missing",
  optionalMissingLabel = "Optional"
}: {
  title: string;
  note: string;
  configured?: boolean;
  optional?: boolean;
  readyLabel?: string;
  missingLabel?: string;
  optionalMissingLabel?: string;
}) {
  return (
    <div className="service-row">
      <span className={cn("service-icon", configured && "is-configured")}>
        {configured ? <Check size={14} aria-hidden="true" /> : null}
      </span>
      <span className="service-copy">
        <strong>{title}</strong>
        <small>{note}</small>
      </span>
      <Badge tone={configured ? "success" : optional ? "neutral" : "warning"}>
        {configured
          ? readyLabel
          : optional
            ? optionalMissingLabel
            : missingLabel}
      </Badge>
    </div>
  );
}
