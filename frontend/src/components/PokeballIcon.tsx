export default function PokeballIcon({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 128 128"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="inline-block align-middle"
    >
      <circle
        cx="64"
        cy="64"
        r="58"
        stroke="white"
        strokeOpacity="0.9"
        strokeWidth="8"
      />

      {/* Top half */}
      <path
        d="M10 64C15 35 37 14 64 14C91 14 113 35 118 64H10Z"
        fill="#ff2a2a"
        fillOpacity="0.85"
      />

      {/* Bottom half (NEW) */}
      <path
        d="M10 64C15 93 37 114 64 114C91 114 113 93 118 64H10Z"
        fill="#ffffff"
        fillOpacity="0.95"
      />

      {/* Middle line */}
      <path
        d="M10 64H118"
        stroke="black"
        strokeOpacity="0.65"
        strokeWidth="10"
      />

      {/* Center button */}
      <circle
        cx="64"
        cy="64"
        r="18"
        fill="black"
        fillOpacity="0.99"
        stroke="black"
        strokeWidth="8"
      />
      <circle cx="64" cy="64" r="6" fill="white" fillOpacity="0.85" />
    </svg>
  );
}
