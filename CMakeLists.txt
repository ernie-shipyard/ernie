cmake_minimum_required(VERSION 3.22)

project(ernie VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(FetchContent)

# Pinned to a release so local builds stay reproducible. To use a local
# checkout instead, pass -DJUCE_SOURCE_DIR=/path/to/JUCE.
set(JUCE_VERSION 9.0.4)

if(JUCE_SOURCE_DIR)
    add_subdirectory("${JUCE_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/juce" EXCLUDE_FROM_ALL)
else()
    FetchContent_Declare(
        JUCE
        GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
        GIT_TAG        ${JUCE_VERSION}
        GIT_SHALLOW    TRUE
    )
    FetchContent_MakeAvailable(JUCE)
endif()

# JUCE requests the VST3 chord and then drops it. Reapply the key mapping on
# every configure; the script is a no-op once the markers are present.
set(_juce_root "${JUCE_SOURCE_DIR}")
if(NOT _juce_root AND DEFINED juce_SOURCE_DIR)
    set(_juce_root "${juce_SOURCE_DIR}")
endif()
if(_juce_root)
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -E env python3 "${CMAKE_SOURCE_DIR}/patches/apply_host_key.py" "${_juce_root}"
        RESULT_VARIABLE _juce_patch_result
    )
    if(NOT _juce_patch_result EQUAL 0)
        message(FATAL_ERROR "Failed to surface the VST3 host key in ${_juce_root}")
    endif()
endif()

juce_add_plugin(Ernie
    COMPANY_NAME "sialboat"
    BUNDLE_ID com.sialboat.erniestereo
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    IS_MIDI_EFFECT FALSE
    EDITOR_WANTS_KEYBOARD_FOCUS FALSE
    COPY_PLUGIN_AFTER_BUILD FALSE
    PLUGIN_MANUFACTURER_CODE Slbt
    PLUGIN_CODE Ers1
    FORMATS AU VST3 Standalone
    PRODUCT_NAME "ernie stereo"
    NEEDS_WEB_BROWSER FALSE
    NEEDS_CURL FALSE)

target_sources(Ernie
    PRIVATE
        source/PluginProcessor.cpp
        source/PluginEditor.cpp
        source/TrackRecorder.cpp
        source/MetadataWriter.cpp
        source/RecordingApi.cpp
        source/WaveformDisplay.cpp
        source/FileMetadata.cpp)

target_compile_definitions(Ernie
    PUBLIC
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_VST3_CAN_REPLACE_VST2=0
        JUCE_STRICT_REFCOUNTEDPOINTER=1)

target_link_libraries(Ernie
    PRIVATE
        juce::juce_audio_utils
        juce::juce_dsp
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags)

if(ERNIE_BUILD_TESTS)
    juce_add_console_app(ErnieRecorderTest PRODUCT_NAME "Ernie Recorder Test")
    target_sources(ErnieRecorderTest PRIVATE tests/RecorderTest.cpp source/TrackRecorder.cpp)
    target_compile_definitions(ErnieRecorderTest PRIVATE
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_MODAL_LOOPS_PERMITTED=0
        JUCE_STANDALONE_APPLICATION=1)
    target_include_directories(ErnieRecorderTest PRIVATE source)
    target_link_libraries(ErnieRecorderTest
        PRIVATE
            juce::juce_audio_basics
            juce::juce_dsp
            juce::juce_gui_basics
        PUBLIC
            juce::juce_recommended_config_flags
            juce::juce_recommended_warning_flags)

    juce_add_console_app(ErnieApiTest PRODUCT_NAME "Ernie API Test")
    target_sources(ErnieApiTest PRIVATE tests/ApiTest.cpp source/RecordingApi.cpp)
    target_compile_definitions(ErnieApiTest PRIVATE
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_STANDALONE_APPLICATION=1)
    target_include_directories(ErnieApiTest PRIVATE source)
    target_link_libraries(ErnieApiTest
        PRIVATE
            juce::juce_core
            juce::juce_events
            juce::juce_gui_basics
        PUBLIC
            juce::juce_recommended_config_flags
            juce::juce_recommended_warning_flags)
endif()
