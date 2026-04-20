import { Composition } from 'remotion';
import { SolarTransition, solarTransitionSchema } from './compositions/SolarTransition';

/**
 * Root entry point for Remotion's bundler. Keep the id `SolarTransition`
 * stable — the server code and the CLI render script both reference it.
 */
export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="SolarTransition"
        component={SolarTransition}
        durationInFrames={180}
        fps={30}
        width={1080}
        height={1080}
        schema={solarTransitionSchema}
        defaultProps={{
          beforeImageUrl: 'https://via.placeholder.com/1080',
          afterImageUrl: 'https://via.placeholder.com/1080',
          kwp: 12,
          yearlySavingsEur: 2400,
          paybackYears: 6.5,
          co2TonnesLifetime: 90,
          tenantName: 'SolarLead',
          brandPrimaryColor: '#0F766E',
          brandLogoUrl: undefined,
        }}
      />
    </>
  );
};
