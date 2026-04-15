import { Composition } from 'remotion';
import { SolarTransition, solarTransitionSchema } from './compositions/SolarTransition';

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
          tenantName: 'SolarLead',
        }}
      />
    </>
  );
};
