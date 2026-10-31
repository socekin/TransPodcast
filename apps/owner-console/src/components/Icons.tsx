import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Stroke({ size = 18, children, ...props }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

function Fill({ size = 18, children, ...props }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export const BackIcon = (props: IconProps) => (
  <Stroke {...props}>
    <path d="M15 18l-6-6 6-6" />
  </Stroke>
);

export const ChevronRightIcon = (props: IconProps) => (
  <Stroke {...props}>
    <path d="m9 18 6-6-6-6" />
  </Stroke>
);

export const ChevronDownIcon = (props: IconProps) => (
  <Stroke {...props}>
    <path d="m6 9 6 6 6-6" />
  </Stroke>
);

export const FeedIcon = (props: IconProps) => (
  <Stroke {...props}>
    <path d="M4 11a9 9 0 0 1 9 9" />
    <path d="M4 4a16 16 0 0 1 16 16" />
    <circle cx="5" cy="19" r="1.2" />
  </Stroke>
);

export const SlidersIcon = (props: IconProps) => (
  <Stroke {...props}>
    <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
    <circle cx="16" cy="7" r="2" />
    <circle cx="10" cy="17" r="2" />
  </Stroke>
);

export const SearchIcon = (props: IconProps) => (
  <Stroke {...props}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m16 16 4 4" />
  </Stroke>
);

export const CloseIcon = (props: IconProps) => (
  <Stroke {...props}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Stroke>
);

export const CheckIcon = (props: IconProps) => (
  <Stroke strokeWidth={2} {...props}>
    <path d="M20 6 9 17l-5-5" />
  </Stroke>
);

export const AlertIcon = (props: IconProps) => (
  <Stroke {...props}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5v5.5M12 16.5h.01" />
  </Stroke>
);

export const CopyIcon = (props: IconProps) => (
  <Stroke {...props}>
    <rect x="9" y="9" width="11" height="11" rx="2.5" />
    <path d="M5 15V6a2 2 0 0 1 2-2h8" />
  </Stroke>
);

export const RewindIcon = (props: IconProps) => (
  <Stroke {...props}>
    <path d="M3 12a9 9 0 1 0 3-6.7" />
    <path d="M3 4v4.5h4.5" />
  </Stroke>
);

export const ForwardIcon = (props: IconProps) => (
  <Stroke {...props}>
    <path d="M21 12a9 9 0 1 1-3-6.7" />
    <path d="M21 4v4.5h-4.5" />
  </Stroke>
);

export const PreviousIcon = (props: IconProps) => (
  <Fill {...props}>
    <rect x="5" y="5" width="2.2" height="14" rx="1" />
    <path d="M19 5v14L8.5 12z" />
  </Fill>
);

export const NextIcon = (props: IconProps) => (
  <Fill {...props}>
    <rect x="16.8" y="5" width="2.2" height="14" rx="1" />
    <path d="M5 5v14l10.5-7z" />
  </Fill>
);

export const PlayIcon = (props: IconProps) => (
  <Fill {...props}>
    <path d="M7 5v14l12-7z" />
  </Fill>
);

export const PauseIcon = (props: IconProps) => (
  <Fill {...props}>
    <rect x="6" y="5" width="4" height="14" rx="1" />
    <rect x="14" y="5" width="4" height="14" rx="1" />
  </Fill>
);

export const MoreIcon = (props: IconProps) => (
  <Fill {...props}>
    <circle cx="5" cy="12" r="1.6" />
    <circle cx="12" cy="12" r="1.6" />
    <circle cx="19" cy="12" r="1.6" />
  </Fill>
);
