// C ABI over WebRTC AudioProcessing (AEC3) for host/c3/echo.py. Mono 10 ms int16 frames.
// Built against webrtc-audio-processing-1 1.3 headers (see Makefile); links the system .so.
#include <modules/audio_processing/include/audio_processing.h>
#include <api/scoped_refptr.h>

#include <string>

namespace webrtc { namespace field_trial { void InitFieldTrialsFromString(const char *trials); } }
using webrtc::AudioProcessing;

struct C3Aec {
  rtc::scoped_refptr<AudioProcessing> apm;
  webrtc::StreamConfig cfg;
  int delay_ms = 0;
};

extern "C" {

// Process-wide WebRTC field trials ("Name/value/Name2/value2/"), read when an
// AudioProcessing is created; the only way to tune the AEC3 suppressor in 1.3.
void c3aec_field_trials(const char *trials) {
  static std::string keep;
  keep = trials ? trials : "";
  webrtc::field_trial::InitFieldTrialsFromString(keep.c_str());
}

void *c3aec_create(int rate, int noise_suppression) {
  AudioProcessing *raw = webrtc::AudioProcessingBuilder().Create();
  if (!raw) return nullptr;
  C3Aec *s = new C3Aec;
  s->apm = rtc::scoped_refptr<AudioProcessing>(raw);
  s->cfg = webrtc::StreamConfig(rate, 1);
  AudioProcessing::Config c;
  c.echo_canceller.enabled = true;
  c.echo_canceller.mobile_mode = false;
  c.high_pass_filter.enabled = true;
  c.noise_suppression.enabled = noise_suppression != 0;
  c.noise_suppression.level = AudioProcessing::Config::NoiseSuppression::kModerate;
  c.gain_controller1.enabled = false;
  c.gain_controller2.enabled = false;
  s->apm->ApplyConfig(c);
  s->apm->Initialize();
  return s;
}

void c3aec_destroy(void *h) { delete static_cast<C3Aec *>(h); }

int c3aec_set_stream_delay_ms(void *h, int ms) {
  C3Aec *s = static_cast<C3Aec *>(h);
  s->delay_ms = ms;
  return s->apm->set_stream_delay_ms(ms);
}

int c3aec_process_reverse(void *h, int16_t *frame) {
  C3Aec *s = static_cast<C3Aec *>(h);
  return s->apm->ProcessReverseStream(frame, s->cfg, s->cfg, frame);
}

int c3aec_process(void *h, int16_t *frame) {
  C3Aec *s = static_cast<C3Aec *>(h);
  s->apm->set_stream_delay_ms(s->delay_ms);  // must precede every ProcessStream
  return s->apm->ProcessStream(frame, s->cfg, s->cfg, frame);
}

// out[0]=ERL dB, out[1]=ERLE dB, out[2]=delay_ms, out[3]=divergent fraction,
// out[4]=residual echo likelihood; NaN when unavailable. Returns 0.
int c3aec_stats(void *h, double *out) {
  C3Aec *s = static_cast<C3Aec *>(h);
  webrtc::AudioProcessingStats st = s->apm->GetStatistics();
  const double nan = __builtin_nan("");
  out[0] = st.echo_return_loss ? *st.echo_return_loss : nan;
  out[1] = st.echo_return_loss_enhancement ? *st.echo_return_loss_enhancement : nan;
  out[2] = st.delay_ms ? double(*st.delay_ms) : nan;
  out[3] = st.divergent_filter_fraction ? *st.divergent_filter_fraction : nan;
  out[4] = st.residual_echo_likelihood ? *st.residual_echo_likelihood : nan;
  return 0;
}

}  // extern "C"
