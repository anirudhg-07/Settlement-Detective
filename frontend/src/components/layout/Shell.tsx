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

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [health, setHealth] = useState<Health | null>(null);
  
  // Format the run ID to look a bit cleaner, e.g., run_20260824191455_20260131 -> 2026-01-31
  let runDate = "Pending";
  if (health?.latest_run) {
    const parts = health.latest_run.split("_");
    if (parts.length >= 3) {
      const datePart = parts[2]; // e.g., 20260131
      if (datePart.length === 8) {
        runDate = `${datePart.substring(0, 4)}-${datePart.substring(4, 6)}-${datePart.substring(6, 8)}`;
      } else {
        runDate = health.latest_run;
      }
    } else {
      runDate = health.latest_run;
    }
  }

  useEffect(() => {
    fetch("/api/health")
      .then((res) => res.json())
      .then((data) => setHealth(data))
      .catch(() => setHealth(null));
  }, []);

  const sections = [
    {
      title: "OVERVIEW",
      items: [{ name: "Command Center", href: "/" }],
    },
    {
      title: "INVESTIGATE",
      items: [{ name: "Exception Queue", href: "/queue" }],
    },
    {
      title: "INSIGHTS",
      items: [{ name: "Analytics", href: "/analytics" }],
    },
  ];

  return (
    <div className={styles.container}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <div className={styles.brandTitle}>SETTLEMENT</div>
          <div className={styles.brandSubtitle}>DETECTIVE</div>
        </div>
        
        <div className={styles.navSections}>
          {sections.map((section) => (
            <div key={section.title} className={styles.navSection}>
              <div className={styles.sectionHeader}>{section.title}</div>
              <nav className={styles.nav}>
                {section.items.map((item) => {
                  const isActive =
                    item.href === "/"
                      ? pathname === "/"
                      : pathname.startsWith(item.href);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={`${styles.navLink} ${
                        isActive ? styles.navLinkActive : ""
                      }`}
                    >
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
          <div className={styles.statusIntact}>
            <span className={styles.statusDot}></span> Chain intact
          </div>
        </div>
      </aside>
      <main className={styles.main}>
        <div className={styles.content}>{children}</div>
      </main>
    </div>
  );
}
