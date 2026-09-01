"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import styles from "./Shell.module.css";

interface Health {
  status: string;
  database: boolean;
  dataset_loaded: boolean;
  payments: number;
  latest_run: string | null;
}

// Inline SVG icons — no external library
const IconGrid = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
    <rect x="1" y="1" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
    <rect x="9" y="1" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
    <rect x="1" y="9" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
    <rect x="9" y="9" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
  </svg>
);

const IconList = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
    <path d="M2 4h12M2 8h12M2 12h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
  </svg>
);

const IconChart = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
    <path d="M2 13V7l4-3 4 4 4-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    <circle cx="14" cy="3" r="1.5" fill="currentColor"/>
  </svg>
);

const IconShield = () => (
  <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
    <path d="M11 2L4 5v5c0 4.4 3 8.5 7 9.5C15 18.5 18 14.4 18 10V5L11 2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
    <path d="M8 11l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [health, setHealth] = useState<Health | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);

  let runDate = "—";
  if (health?.latest_run) {
    const parts = health.latest_run.split("_");
    if (parts.length >= 3) {
      const d = parts[2];
      if (d.length === 8) runDate = `${d.substring(0, 4)}-${d.substring(4, 6)}-${d.substring(6, 8)}`;
      else runDate = health.latest_run;
    } else runDate = health.latest_run;
  }

  useEffect(() => {
    fetch("/api/health")
      .then(r => r.json())
      .then(d => setHealth(d))
      .catch(() => {});
  }, []);

  const sections = [
    {
      title: "OVERVIEW",
      items: [{ name: "Command Center", href: "/", icon: <IconGrid /> }],
    },
    {
      title: "INVESTIGATE",
      items: [{ name: "Exception Queue", href: "/queue", icon: <IconList /> }],
    },
    {
      title: "INSIGHTS",
      items: [{ name: "Analytics", href: "/analytics", icon: <IconChart /> }],
    },
  ];

  return (
    <div className={styles.container}>
      {/* Mobile top bar */}
      <div className={styles.mobileBar}>
        <div className={styles.mobileBrand}>
          <IconShield />
          <span>Settlement Detective</span>
        </div>
        <button className={styles.mobileMenuBtn} onClick={() => setMobileOpen(!mobileOpen)} aria-label="Toggle menu">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </button>
      </div>

      {/* Mobile dropdown overlay */}
      {mobileOpen && (
        <div className={styles.mobileDropdown} onClick={() => setMobileOpen(false)}>
          {sections.flatMap(s => s.items).map(item => (
            <Link
              key={item.href}
              href={item.href}
              className={`${styles.mobileNavLink} ${
                (item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)) ? styles.mobileNavLinkActive : ""
              }`}
            >
              {item.icon}
              {item.name}
            </Link>
          ))}
        </div>
      )}

      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <div className={styles.brandIcon}><IconShield /></div>
          <div>
            <div className={styles.brandTitle}>SETTLEMENT</div>
            <div className={styles.brandSubtitle}>DETECTIVE</div>
          </div>
        </div>

        <div className={styles.navSections}>
          {sections.map(section => (
            <div key={section.title} className={styles.navSection}>
              <div className={styles.sectionHeader}>{section.title}</div>
              <nav className={styles.nav}>
                {section.items.map(item => {
                  const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={`${styles.navLink} ${isActive ? styles.navLinkActive : ""}`}
                    >
                      <span className={styles.navIcon}>{item.icon}</span>
                      {item.name}
                    </Link>
                  );
                })}
              </nav>
            </div>
          ))}
        </div>

        <div className={styles.statusFooter}>
          <div className={styles.statusLabel}>CURRENT RUN</div>
          <div className={styles.statusDate}>{runDate}</div>
          <div className={styles.statusItems}>
            <div className={styles.statusIntact}>
              <span className={styles.statusDot} />
              Chain intact
            </div>
            {health && (
              <div className={styles.statusPayments}>
                {health.payments.toLocaleString()} payments
              </div>
            )}
          </div>
        </div>
      </aside>

      <main className={styles.main}>
        <div className={styles.content}>{children}</div>
      </main>
    </div>
  );
}
