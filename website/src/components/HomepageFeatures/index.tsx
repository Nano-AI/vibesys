import type {ReactNode} from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

type FeatureItem = {
  title: string;
  icon: ReactNode;
  description: ReactNode;
};

const FeatureList: FeatureItem[] = [
  {
    title: 'Bespoke by target',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="7" />
        <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
      </svg>
    ),
    description: (
      <>
        Each target defines its own implementation contract, correctness checks,
        and performance benchmark, so VibeSys works across domains instead of
        assuming one runtime, language, or deployment shape.
      </>
    ),
  },
  {
    title: 'Multi-agent optimization loop',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M4 12a8 8 0 0 1 14-5M20 12a8 8 0 0 1-14 5" />
        <path d="M18 4v3h-3M6 20v-3h3" />
      </svg>
    ),
    description: (
      <>
        An outer loop plans the search over designs; an inner loop implements and
        validates candidates. An independent judge checks correctness and
        reward-hacking risk before results are recorded.
      </>
    ),
  },
  {
    title: 'Extend with skills, not forks',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="4" y="4" width="7" height="7" rx="1" />
        <rect x="13" y="4" width="7" height="7" rx="1" />
        <rect x="4" y="13" width="7" height="7" rx="1" />
        <rect x="13" y="13" width="7" height="7" rx="1" />
      </svg>
    ),
    description: (
      <>
        New model families, hardware platforms, and optimizations are added by
        writing an Agent Skill distilled from real serving engines and research,
        not by modifying the framework.
      </>
    ),
  },
];

function Feature({title, icon, description}: FeatureItem) {
  return (
    <div className={clsx('col col--4')}>
      <div className="text--center">
        <span className={styles.featureIcon} aria-hidden="true">
          {icon}
        </span>
      </div>
      <div className="text--center padding-horiz--md">
        <Heading as="h3">{title}</Heading>
        <p>{description}</p>
      </div>
    </div>
  );
}

export default function HomepageFeatures(): ReactNode {
  return (
    <section className={styles.features}>
      <div className="container">
        <div className="row">
          {FeatureList.map((props, idx) => (
            <Feature key={idx} {...props} />
          ))}
        </div>
      </div>
    </section>
  );
}
