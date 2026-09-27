//! What CoreAudio knows about the default output that cpal does not pass on:
//! the latency of its output stream, and which speaker each of its channels
//! is.
//!
//! cpal's playback timestamp covers the device's buffer, its latency and its
//! safety offset, but not the stream's own latency -- 19.7 ms on a MacBook
//! Air's speakers, measured, and the part a Bluetooth sink reports its radio
//! link in. mpv adds it; so does the mixer here. And a multichannel device
//! states which speaker each channel is (`kAudioDevicePropertyPreferredChannelLayout`,
//! set in Audio MIDI Setup), which is what makes playing 5.1 and 7.1 to it
//! safe: the samples go out in the device's own order.

use std::ffi::c_void;
use std::ptr::{self, NonNull};
use std::time::Duration;

use objc2_audio_toolbox::{
    AudioFormatGetProperty, AudioFormatGetPropertyInfo,
    kAudioFormatProperty_ChannelLayoutForBitmap, kAudioFormatProperty_ChannelLayoutForTag,
};
use objc2_core_audio::{
    AudioObjectGetPropertyData, AudioObjectGetPropertyDataSize, AudioObjectID,
    AudioObjectPropertyAddress, AudioObjectPropertyScope, AudioObjectPropertySelector,
    kAudioDevicePropertyNominalSampleRate, kAudioDevicePropertyPreferredChannelLayout,
    kAudioDevicePropertyStreams, kAudioHardwarePropertyDefaultOutputDevice,
    kAudioObjectPropertyElementMain, kAudioObjectPropertyScopeGlobal,
    kAudioObjectPropertyScopeOutput, kAudioObjectSystemObject, kAudioStreamPropertyLatency,
};
use objc2_core_audio_types::{
    AudioChannelDescription, AudioChannelLayout, kAudioChannelLayoutTag_UseChannelBitmap,
    kAudioChannelLayoutTag_UseChannelDescriptions,
};

fn address(
    selector: AudioObjectPropertySelector,
    scope: AudioObjectPropertyScope,
) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress {
        mSelector: selector,
        mScope: scope,
        mElement: kAudioObjectPropertyElementMain,
    }
}

/// A fixed-size property, or None when the object does not have it.
fn read<T: Copy>(
    object: AudioObjectID,
    selector: AudioObjectPropertySelector,
    scope: AudioObjectPropertyScope,
    mut value: T,
) -> Option<T> {
    let mut address = address(selector, scope);
    let mut size = size_of::<T>() as u32;
    // SAFETY: `value` has room for the `size` bytes asked for.
    let status = unsafe {
        AudioObjectGetPropertyData(
            object,
            NonNull::from(&mut address),
            0,
            ptr::null(),
            NonNull::from(&mut size),
            NonNull::from(&mut value).cast(),
        )
    };
    (status == 0 && size as usize == size_of::<T>()).then_some(value)
}

/// A variable-size property as raw bytes, 8-byte aligned for the structs
/// read out of it.
fn read_bytes(
    object: AudioObjectID,
    selector: AudioObjectPropertySelector,
    scope: AudioObjectPropertyScope,
) -> Option<Vec<u64>> {
    let mut address = address(selector, scope);
    let mut size = 0u32;
    // SAFETY: plain out-parameters.
    let status = unsafe {
        AudioObjectGetPropertyDataSize(
            object,
            NonNull::from(&mut address),
            0,
            ptr::null(),
            NonNull::from(&mut size),
        )
    };
    if status != 0 || size == 0 {
        return None;
    }
    let mut buffer = vec![0u64; (size as usize).div_ceil(8)];
    // SAFETY: the buffer holds at least `size` bytes.
    let status = unsafe {
        AudioObjectGetPropertyData(
            object,
            NonNull::from(&mut address),
            0,
            ptr::null(),
            NonNull::from(&mut size),
            NonNull::new(buffer.as_mut_ptr().cast::<c_void>())?,
        )
    };
    (status == 0).then_some(buffer)
}

/// The system's default output device.
pub(crate) fn default_output() -> Option<AudioObjectID> {
    read(
        kAudioObjectSystemObject as AudioObjectID,
        kAudioHardwarePropertyDefaultOutputDevice,
        kAudioObjectPropertyScopeGlobal,
        0,
    )
    .filter(|&device| device != 0)
}

/// The latency of `device`'s first output stream.
pub(crate) fn stream_latency(device: AudioObjectID) -> Option<Duration> {
    let streams = read_bytes(
        device,
        kAudioDevicePropertyStreams,
        kAudioObjectPropertyScopeOutput,
    )?;
    // AudioStreamIDs are u32s: the first is the low half of the first word.
    // SAFETY: the buffer holds at least one AudioStreamID.
    let stream = unsafe { streams.as_ptr().cast::<AudioObjectID>().read() };
    let frames = read(
        stream,
        kAudioStreamPropertyLatency,
        kAudioObjectPropertyScopeGlobal,
        0u32,
    )?;
    let rate = read(
        device,
        kAudioDevicePropertyNominalSampleRate,
        kAudioObjectPropertyScopeGlobal,
        0f64,
    )?;
    (rate > 0.0).then(|| Duration::from_secs_f64(f64::from(frames) / rate))
}

/// The speaker each of `device`'s output channels feeds, as CoreAudio
/// channel labels in channel order; None when the device does not say.
pub(crate) fn speakers(device: AudioObjectID) -> Option<Vec<u32>> {
    let bytes = read_bytes(
        device,
        kAudioDevicePropertyPreferredChannelLayout,
        kAudioObjectPropertyScopeOutput,
    )?;
    // SAFETY: CoreAudio filled the buffer with an AudioChannelLayout.
    let layout = unsafe { &*bytes.as_ptr().cast::<AudioChannelLayout>() };
    let tag = layout.mChannelLayoutTag;
    if tag == kAudioChannelLayoutTag_UseChannelDescriptions {
        // SAFETY: the descriptions follow in the same buffer, as many as it
        // says.
        Some(unsafe { descriptions(layout) })
    } else if tag == kAudioChannelLayoutTag_UseChannelBitmap {
        expand(
            kAudioFormatProperty_ChannelLayoutForBitmap,
            &layout.mChannelBitmap,
        )
    } else {
        expand(kAudioFormatProperty_ChannelLayoutForTag, &tag)
    }
}

/// # Safety
/// `layout` must be followed in memory by its channel descriptions.
unsafe fn descriptions(layout: &AudioChannelLayout) -> Vec<u32> {
    let first = layout.mChannelDescriptions.as_ptr();
    (0..layout.mNumberChannelDescriptions as usize)
        // SAFETY: per the caller.
        .map(|i| unsafe { (*first.add(i)).mChannelLabel })
        .collect()
}

/// A layout named by a tag or bitmap, as the labels CoreAudio expands it to.
fn expand<T>(property: u32, specifier: &T) -> Option<Vec<u32>> {
    let specifier_size = size_of::<T>() as u32;
    let specifier = ptr::from_ref(specifier).cast::<c_void>();
    let mut size = 0u32;
    // SAFETY: the specifier is a tag or bitmap of the size given.
    let status = unsafe {
        AudioFormatGetPropertyInfo(
            property,
            specifier_size,
            specifier,
            NonNull::from(&mut size),
        )
    };
    if status != 0 || (size as usize) < size_of::<AudioChannelLayout>() {
        return None;
    }
    let mut buffer = vec![0u64; (size as usize).div_ceil(8)];
    // SAFETY: the buffer holds `size` bytes.
    let status = unsafe {
        AudioFormatGetProperty(
            property,
            specifier_size,
            specifier,
            &raw mut size,
            buffer.as_mut_ptr().cast(),
        )
    };
    if status != 0 {
        return None;
    }
    // SAFETY: AudioToolbox filled it with a layout followed by its
    // descriptions.
    Some(unsafe { descriptions(&*buffer.as_ptr().cast::<AudioChannelLayout>()) })
}

// Keeps the description type named where its layout is relied on above.
const _: () = assert!(size_of::<AudioChannelDescription>() == 20);
