import type {ReactNode} from 'react';
import styles from './styles.module.css';

/**
 * Native SVG redraw of the architecture figure (previously a static PNG).
 * Two variants share the same icon set and region/zone language:
 * a three-column desktop diagram (grid-aligned by hand, y=16..496 band
 * across all three regions) and a single-column mobile diagram (stacked
 * list rows) swapped in below the 768px breakpoint, since the desktop
 * layout's text becomes illegible once it's scaled down to phone width.
 */

const ModelIcon = (
  <>
    <circle cx={12} cy={5.5} r={2} />
    <circle cx={5.5} cy={18} r={2} />
    <circle cx={18.5} cy={18} r={2} />
    <path d="M10.8 7.3L7 15.5M13.2 7.3l3.8 8.2" />
  </>
);

const HardwareIcon = (
  <>
    <rect x={6} y={6} width={12} height={12} rx={1.5} />
    <path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" />
  </>
);

const WorkloadIcon = (
  <>
    <rect x={4.5} y={13} width={3.4} height={7} fill="currentColor" stroke="none" />
    <rect x={10.3} y={9} width={3.4} height={11} fill="currentColor" stroke="none" />
    <rect x={16.1} y={4} width={3.4} height={16} fill="currentColor" stroke="none" />
  </>
);

const SearchPolicyIcon = (
  <>
    <circle cx={6} cy={6} r={2} />
    <circle cx={6} cy={18} r={2} />
    <circle cx={18} cy={6} r={2} />
    <path d="M6 8v8M8 6h8a2 2 0 0 1-2 2h-2a4 4 0 0 0-4 4" />
  </>
);

const SearchStateIcon = (
  <>
    <ellipse cx={12} cy={6} rx={7} ry={3} />
    <path d="M5 6v12a7 3 0 0 0 14 0V6" />
    <path d="M5 12a7 3 0 0 0 14 0" />
  </>
);

const ImplementerIcon = <path d="M8 6L3 12l5 6M16 6l5 6-5 6" />;

const AccuracyJudgeIcon = (
  <>
    <circle cx={12} cy={12} r={8} />
    <path d="M8.5 12.5l2.5 2.5 5-5" />
  </>
);

const ProfilerIcon = (
  <>
    <path d="M4 16a8 8 0 0 1 16 0" />
    <path d="M12 16l4-5" />
    <circle cx={12} cy={16} r={1.3} fill="currentColor" stroke="none" />
  </>
);

const SkillsLibraryIcon = (
  <>
    <path d="M6 3h9l4 4v14H6z" />
    <path d="M9 11h6M9 14h6M9 17h4" />
  </>
);

const ExecutionEnvIcon = (
  <>
    <rect x={3} y={4} width={18} height={16} rx={1} />
    <path d="M7 9l3 3-3 3M13 15h4" />
  </>
);

const FileTreeIcon = (
  <>
    <path d="M4 5h6l2 2h8v12H4z" />
    <path d="M4 5v14" />
  </>
);

const TestIcon = (
  <>
    <path d="M9 3h6M10 3v5l-5.5 9.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-3.5L14 8V3" />
    <path d="M7.5 15h9" />
  </>
);

function Icon({
  x,
  y,
  size = 22,
  children,
}: {
  x: number;
  y: number;
  size?: number;
  children: ReactNode;
}) {
  return (
    <svg
      x={x}
      y={y}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={styles.icon}>
      {children}
    </svg>
  );
}

function LeafCard({
  x,
  y,
  w,
  h,
  icon,
  title,
  subtitle,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  icon: ReactNode;
  title: string[];
  subtitle?: string[];
}) {
  const iconX = x + 13;
  const iconY = y + 11;
  const textX = x + 13;
  let ty = iconY + 22 + 19;
  const titleLines = title.map((line, i) => {
    const el = (
      <tspan key={i} x={textX} y={ty}>
        {line}
      </tspan>
    );
    ty += 18;
    return el;
  });
  ty += 3;
  const subtitleLines = (subtitle ?? []).map((line, i) => {
    const el = (
      <tspan key={i} x={textX} y={ty}>
        {line}
      </tspan>
    );
    ty += 16;
    return el;
  });

  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={6} className={styles.card} />
      <Icon x={iconX} y={iconY}>
        {icon}
      </Icon>
      <text className={styles.cardTitle}>{titleLines}</text>
      {subtitle && <text className={styles.cardSubtitle}>{subtitleLines}</text>}
    </g>
  );
}

/** Compact single-line row used by the mobile diagram: icon + title + optional dim detail, inline. */
function ListRow({
  x,
  y,
  w,
  h,
  icon,
  title,
  dim,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  icon: ReactNode;
  title: string;
  dim?: string;
}) {
  const iconSize = 18;
  const iconX = x + 10;
  const iconY = y + (h - iconSize) / 2;
  const textX = iconX + iconSize + 10;
  const textY = y + h / 2 + 5;

  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={6} className={styles.card} />
      <Icon x={iconX} y={iconY} size={iconSize}>
        {icon}
      </Icon>
      <text x={textX} y={textY} className={styles.cardTitle}>
        {title}
        {dim && <tspan className={styles.cardSubtitle}> · {dim}</tspan>}
      </text>
    </g>
  );
}

const ARROW_MARKER = 'url(#arch-arrow)';

function ArrowMarkerDefs() {
  return (
    <defs>
      <marker
        id="arch-arrow"
        viewBox="0 0 10 10"
        refX="8"
        refY="5"
        markerWidth="7"
        markerHeight="7"
        orient="auto-start-reverse">
        <path d="M0 0L10 5L0 10z" className={styles.arrowHead} />
      </marker>
    </defs>
  );
}

function DesktopDiagram() {
  return (
    <svg
      className={styles.diagram}
      viewBox="0 0 1160 520"
      role="img"
      aria-label="Diagram: user input (model, hardware, workload) feeds VibeSys, where an outer loop plans the search and an inner loop of Implementer, Accuracy Judge, and Profiler implements, checks, and benchmarks each candidate, producing a bespoke system. On error, the Accuracy Judge sends the candidate back to the Implementer.">
      <ArrowMarkerDefs />

      {/* ---- Region A: User Input ---- */}
      <rect x={16} y={16} width={216} height={480} rx={14} className={styles.regionOutline} />
      <text x={32} y={44} className={styles.zoneLabel}>
        USER INPUT
      </text>

      <LeafCard
        x={32}
        y={62}
        w={184}
        h={108}
        title={['Model']}
        subtitle={['weights, code']}
        icon={ModelIcon}
      />
      <LeafCard
        x={32}
        y={217}
        w={184}
        h={108}
        title={['Hardware']}
        subtitle={['H100, M4 Pro, …']}
        icon={HardwareIcon}
      />
      <LeafCard
        x={32}
        y={372}
        w={184}
        h={108}
        title={['Workload']}
        subtitle={['benchmark metrics']}
        icon={WorkloadIcon}
      />

      {/* arrow: User Input -> VibeSys */}
      <line x1={236} y1={256} x2={256} y2={256} className={styles.arrow} markerEnd={ARROW_MARKER} />

      {/* ---- Region B: VibeSys ---- */}
      <rect x={260} y={16} width={560} height={480} rx={14} className={styles.regionOutline} />
      <text x={280} y={52} className={styles.title}>
        VibeSys
      </text>

      {/* Outer Loop zone */}
      <rect x={276} y={70} width={528} height={154} rx={10} className={styles.zoneOutline} />
      <text x={288} y={88} className={styles.zoneLabel}>
        OUTER LOOP
      </text>
      <LeafCard
        x={292}
        y={100}
        w={248}
        h={112}
        title={['Search Policy']}
        subtitle={['design decisions', 'branch / backtrack']}
        icon={SearchPolicyIcon}
      />
      <LeafCard
        x={556}
        y={100}
        w={232}
        h={112}
        title={['Search State']}
        subtitle={['git state', 'feedback history']}
        icon={SearchStateIcon}
      />
      <line
        x1={554}
        y1={156}
        x2={542}
        y2={156}
        className={styles.arrowMuted}
        markerEnd={ARROW_MARKER}
      />

      {/* Base Commit + Task / Commit + Feedback connectors */}
      <text x={400} y={242} textAnchor="middle" className={styles.flowLabel}>
        Base Commit + Task
      </text>
      <line x1={400} y1={246} x2={400} y2={259} className={styles.arrow} markerEnd={ARROW_MARKER} />
      <text x={680} y={242} textAnchor="middle" className={styles.flowLabel}>
        Commit + Feedback
      </text>
      <line x1={680} y1={259} x2={680} y2={246} className={styles.arrow} markerEnd={ARROW_MARKER} />

      {/* Inner Loop zone */}
      <rect x={276} y={264} width={528} height={122} rx={10} className={styles.zoneOutline} />
      <text x={288} y={282} className={styles.zoneLabel}>
        INNER LOOP
      </text>

      {/* Error: Accuracy Judge sends the candidate back to the Implementer */}
      <path
        d="M540 298 C 500 284, 407 284, 367 298"
        className={styles.arrowDashed}
        markerEnd={ARROW_MARKER}
      />
      <text x={454} y={280} textAnchor="middle" className={styles.errorLabel}>
        Error
      </text>

      <LeafCard x={292} y={298} w={150} h={76} title={['Implementer']} icon={ImplementerIcon} />
      <LeafCard
        x={458}
        y={298}
        w={165}
        h={76}
        title={['Accuracy Judge']}
        icon={AccuracyJudgeIcon}
      />
      <LeafCard x={639} y={298} w={150} h={76} title={['Profiler']} icon={ProfilerIcon} />
      <line x1={442} y1={336} x2={458} y2={336} className={styles.arrowMuted} markerEnd={ARROW_MARKER} />
      <line x1={623} y1={336} x2={639} y2={336} className={styles.arrowMuted} markerEnd={ARROW_MARKER} />

      <LeafCard
        x={292}
        y={404}
        w={248}
        h={76}
        title={['Skills Library']}
        icon={SkillsLibraryIcon}
      />
      <LeafCard
        x={556}
        y={404}
        w={232}
        h={76}
        title={['Execution Env.']}
        icon={ExecutionEnvIcon}
      />
      <line
        x1={367}
        y1={404}
        x2={367}
        y2={374}
        className={styles.arrowMuted}
        markerEnd={ARROW_MARKER}
      />
      <line
        x1={590}
        y1={404}
        x2={590}
        y2={374}
        className={styles.arrowMuted}
        markerStart={ARROW_MARKER}
        markerEnd={ARROW_MARKER}
      />
      <line
        x1={740}
        y1={404}
        x2={740}
        y2={374}
        className={styles.arrowMuted}
        markerStart={ARROW_MARKER}
        markerEnd={ARROW_MARKER}
      />

      {/* arrow: VibeSys -> Bespoke System */}
      <line x1={824} y1={256} x2={844} y2={256} className={styles.arrow} markerEnd={ARROW_MARKER} />

      {/* ---- Region C: Bespoke System ---- */}
      <rect x={848} y={16} width={296} height={480} rx={14} className={styles.regionOutline} />
      <text x={864} y={44} className={styles.zoneLabel}>
        BESPOKE SYSTEM
      </text>
      <rect x={864} y={62} width={264} height={418} rx={6} className={styles.card} />
      <text x={880} y={90} className={styles.mono}>
        <tspan x={880} dy={0} fontWeight={600}>
          serving_system/
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'├─ api_server.py'}
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'├─ scheduler.py'}
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'├─ router.py'}
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'├─ cache.py'}
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'├─ model.py'}
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'└─ backend.py'}
        </tspan>
        <tspan x={880} dy={27} fontWeight={600}>
          tests/
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'├─ test_accuracy.py'}
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'└─ test_router.py'}
        </tspan>
        <tspan x={880} dy={27} fontWeight={600}>
          bench/
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'├─ microbench.py'}
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'├─ latency.csv'}
        </tspan>
        <tspan x={880} dy={19} className={styles.monoDim}>
          {'└─ tpt.csv'}
        </tspan>
      </text>
    </svg>
  );
}

/**
 * Simplified variant of the same left-to-right layout as the desktop
 * diagram (User Input -> VibeSys -> Bespoke System, one column each),
 * just with fewer details per card. Rendered at a fixed pixel size
 * (viewBox units == CSS px) rather than scaled to fit the container, so
 * text stays legible; a horizontally scrolling wrapper handles the
 * overflow on narrow screens instead of shrinking the whole diagram.
 */
function MobileDiagram() {
  const narrowRowX = 28;
  const narrowRowW = 126;

  return (
    <svg
      className={styles.diagramMobile}
      viewBox="0 0 680 490"
      role="img"
      aria-label="Diagram: user input (model, hardware, workload) feeds VibeSys, where an outer loop plans the search and an inner loop of Implementer, Accuracy Judge, and Profiler implements, checks, and benchmarks each candidate, producing a bespoke system. On error, the Accuracy Judge sends the candidate back to the Implementer.">
      <ArrowMarkerDefs />

      {/* ---- User Input ---- */}
      <rect x={16} y={16} width={150} height={174} rx={12} className={styles.regionOutline} />
      <text x={28} y={38} className={styles.zoneLabel}>
        USER INPUT
      </text>
      <ListRow x={narrowRowX} y={50} w={narrowRowW} h={38} icon={ModelIcon} title="Model" />
      <ListRow x={narrowRowX} y={94} w={narrowRowW} h={38} icon={HardwareIcon} title="Hardware" />
      <ListRow x={narrowRowX} y={138} w={narrowRowW} h={38} icon={WorkloadIcon} title="Workload" />

      <line x1={166} y1={103} x2={186} y2={103} className={styles.arrow} markerEnd={ARROW_MARKER} />

      {/* ---- VibeSys ---- */}
      <rect x={186} y={16} width={296} height={458} rx={12} className={styles.regionOutline} />
      <text x={200} y={42} className={styles.title}>
        VibeSys
      </text>

      <rect x={194} y={56} width={280} height={120} rx={10} className={styles.zoneOutline} />
      <text x={204} y={72} className={styles.zoneLabel}>
        OUTER LOOP
      </text>
      <ListRow
        x={204}
        y={82}
        w={260}
        h={38}
        icon={SearchPolicyIcon}
        title="Search Policy"
        dim="design decisions"
      />
      <ListRow
        x={204}
        y={126}
        w={260}
        h={38}
        icon={SearchStateIcon}
        title="Search State"
        dim="git + feedback"
      />

      <rect x={194} y={186} width={280} height={274} rx={10} className={styles.zoneOutline} />
      <text x={204} y={202} className={styles.zoneLabel}>
        INNER LOOP
      </text>
      <ListRow x={204} y={212} w={260} h={38} icon={ImplementerIcon} title="Implementer" />
      <ListRow x={204} y={256} w={260} h={38} icon={AccuracyJudgeIcon} title="Accuracy Judge" />
      <text x={242} y={311} className={styles.errorLabel}>
        On error → back to Implementer
      </text>
      <ListRow x={204} y={322} w={260} h={38} icon={ProfilerIcon} title="Profiler" />
      <ListRow x={204} y={366} w={260} h={38} icon={SkillsLibraryIcon} title="Skills Library" />
      <ListRow x={204} y={410} w={260} h={38} icon={ExecutionEnvIcon} title="Execution Env." />

      <line x1={482} y1={245} x2={502} y2={245} className={styles.arrow} markerEnd={ARROW_MARKER} />

      {/* ---- Bespoke System ---- */}
      <rect x={502} y={16} width={150} height={130} rx={12} className={styles.regionOutline} />
      <text x={514} y={38} className={styles.zoneLabel}>
        BESPOKE SYSTEM
      </text>
      <ListRow
        x={narrowRowX + 486}
        y={50}
        w={narrowRowW}
        h={38}
        icon={FileTreeIcon}
        title="serving_system/"
      />
      <ListRow
        x={narrowRowX + 486}
        y={94}
        w={narrowRowW}
        h={38}
        icon={TestIcon}
        title="tests/ + bench/"
      />
    </svg>
  );
}

export default function ArchitectureDiagram(): ReactNode {
  return (
    <figure className={styles.figure}>
      <figcaption className={styles.caption}>
        An outer loop plans the search over designs; an inner loop
        implements and validates candidates; an independent judge checks
        correctness before results are recorded.
      </figcaption>
      <DesktopDiagram />
      <div className={styles.diagramMobileScroll}>
        <MobileDiagram />
      </div>
    </figure>
  );
}
