import { useId } from "react";

/** ClaimMate mark: a speech bubble (the voice assistant) holding an AI sparkle, on a
 *  blue-to-violet gradient. Original artwork in the style of Material 3 AI products. */
export function Logo({ size = 36 }: { size?: number }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 48 48" role="img" aria-label="ClaimMate">
      <defs>
        <linearGradient id={`${id}-g`} x1="4" y1="4" x2="44" y2="44" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#1a73e8" />
          <stop offset="0.55" stopColor="#7b61ff" />
          <stop offset="1" stopColor="#c058d6" />
        </linearGradient>
      </defs>
      {/* speech bubble with a tail at the lower left */}
      <path
        d="M24 4c11 0 20 8.3 20 18.5S35 41 24 41c-2.6 0-5.1-.4-7.3-1.2L8 44l1.9-7.6C6.2 33.1 4 28.1 4 22.5 4 12.3 13 4 24 4z"
        fill={`url(#${id}-g)`}
      />
      {/* main sparkle */}
      <path d="M24 11c.9 6.2 4.8 10.1 11 11-6.2.9-10.1 4.8-11 11-.9-6.2-4.8-10.1-11-11 6.2-.9 10.1-4.8 11-11z" fill="#fff" />
      {/* small companion sparkle */}
      <path d="M34.5 9c.3 2 1.5 3.2 3.5 3.5-2 .3-3.2 1.5-3.5 3.5-.3-2-1.5-3.2-3.5-3.5 2-.3 3.2-1.5 3.5-3.5z" fill="#fff" opacity="0.85" />
    </svg>
  );
}
