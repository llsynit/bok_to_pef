<?xml version="1.0" encoding="ISO-8859-1"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:xs="http://www.w3.org/2001/XMLSchema"
                xmlns:test="http://www.jenitennison.com/xslt/unit-test"
                xmlns:x="http://www.jenitennison.com/xslt/xspec"
                xmlns:__x="http://www.w3.org/1999/XSL/TransformAliasAlias"
                xmlns:pkg="http://expath.org/ns/pkg"
                xmlns:impl="urn:x-xspec:compile:xslt:impl"
                xmlns:epub="http://www.idpf.org/2007/ops"
                xmlns="http://www.w3.org/1999/xhtml"
                version="2.0"
                exclude-result-prefixes="pkg impl">
   <xsl:import href="file:/home/statped/Dokumenter/produksjonssystem/xslt/prepare-for-braille/prepare-for-braille.xsl"/>
   <xsl:import href="file:/home/statped/Dokumenter/produksjonssystem/tests/tools/xspec/src/compiler/generate-tests-utils.xsl"/>
   <xsl:import href="file:/home/statped/Dokumenter/produksjonssystem/tests/tools/xspec/src/schematron/sch-location-compare.xsl"/>
   <xsl:namespace-alias stylesheet-prefix="__x" result-prefix="xsl"/>
   <xsl:variable name="x:stylesheet-uri"
                 as="xs:string"
                 select="'file:/home/statped/Dokumenter/produksjonssystem/xslt/prepare-for-braille/prepare-for-braille.xsl'"/>
   <xsl:output name="x:report" method="xml" indent="yes"/>
   <xsl:template name="x:main">
      <xsl:message>
         <xsl:text>Testing with </xsl:text>
         <xsl:value-of select="system-property('xsl:product-name')"/>
         <xsl:text> </xsl:text>
         <xsl:value-of select="system-property('xsl:product-version')"/>
      </xsl:message>
      <xsl:result-document format="x:report">
         <xsl:processing-instruction name="xml-stylesheet">type="text/xsl" href="file:/home/statped/Dokumenter/produksjonssystem/tests/tools/xspec/src/compiler/format-xspec-report.xsl"</xsl:processing-instruction>
         <x:report stylesheet="{$x:stylesheet-uri}"
                   date="{current-dateTime()}"
                   xspec="file:/home/statped/Dokumenter/produksjonssystem/xslt/./prepare-for-braille/prepare-for-braille.xspec">
            <xsl:call-template name="x:d4e2"/>
            <xsl:call-template name="x:d4e9"/>
         </x:report>
      </xsl:result-document>
   </xsl:template>
   <xsl:template name="x:d4e2">
      <xsl:message>Remove newlines from title elements (test for https://github.com/nlbdev/pipeline/issues/226)</xsl:message>
      <x:scenario>
         <x:label>Remove newlines from title elements (test for https://github.com/nlbdev/pipeline/issues/226)</x:label>
         <x:context xml:space="preserve"><title><xsl:text>  Test
        test  </xsl:text></title></x:context>
         <xsl:variable name="x:result" as="item()*">
            <xsl:variable name="impl:context-doc" as="document-node()">
               <xsl:document>
                  <title>
                     <xsl:text>  Test
        test  </xsl:text>
                  </title>
               </xsl:document>
            </xsl:variable>
            <xsl:variable name="impl:context" select="$impl:context-doc/node()"/>
            <xsl:apply-templates select="$impl:context"/>
         </xsl:variable>
         <xsl:call-template name="test:report-value">
            <xsl:with-param name="value" select="$x:result"/>
            <xsl:with-param name="wrapper-name" select="'x:result'"/>
            <xsl:with-param name="wrapper-ns" select="'http://www.jenitennison.com/xslt/xspec'"/>
         </xsl:call-template>
         <xsl:call-template name="x:d4e6">
            <xsl:with-param name="x:result" select="$x:result"/>
         </xsl:call-template>
      </x:scenario>
   </xsl:template>
   <xsl:template name="x:d4e6">
      <xsl:param name="x:result" required="yes"/>
      <xsl:message>the result should be as expected</xsl:message>
      <xsl:variable name="impl:expected-doc" as="document-node()">
         <xsl:document>
            <title>
               <xsl:text>Test test</xsl:text>
            </title>
         </xsl:document>
      </xsl:variable>
      <xsl:variable name="impl:expected" select="$impl:expected-doc/node()"/>
      <xsl:variable name="impl:successful"
                    as="xs:boolean"
                    select="test:deep-equal($impl:expected, $x:result, 2)"/>
      <xsl:if test="not($impl:successful)">
         <xsl:message>      FAILED</xsl:message>
      </xsl:if>
      <x:test successful="{$impl:successful}">
         <x:label>the result should be as expected</x:label>
         <xsl:call-template name="test:report-value">
            <xsl:with-param name="value" select="$impl:expected"/>
            <xsl:with-param name="wrapper-name" select="'x:expect'"/>
            <xsl:with-param name="wrapper-ns" select="'http://www.jenitennison.com/xslt/xspec'"/>
         </xsl:call-template>
      </x:test>
   </xsl:template>
   <xsl:template name="x:d4e9">
      <xsl:message>Make sure that there's whitespace after sup and sub (test for https://github.com/nlbdev/pipeline/issues/227)</xsl:message>
      <x:scenario>
         <x:label>Make sure that there's whitespace after sup and sub (test for https://github.com/nlbdev/pipeline/issues/227)</x:label>
         <x:context>
            <div>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx</xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text>zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx</xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text>zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx</xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text>zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx</xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text>zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><span><xsl:text> zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><span><xsl:text> zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup></span><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text></span><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub></span><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text></span><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
            </div>
         </x:context>
         <xsl:variable name="x:result" as="item()*">
            <xsl:variable name="impl:context-doc" as="document-node()">
               <xsl:document>
                  <div>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> zzz</xsl:text></p>
                     <p xml:space="preserve"><xsl:text>xxx</xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> zzz</xsl:text></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text>zzz</xsl:text></p>
                     <p xml:space="preserve"><xsl:text>xxx</xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text>zzz</xsl:text></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> zzz</xsl:text></p>
                     <p xml:space="preserve"><xsl:text>xxx</xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> zzz</xsl:text></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text>zzz</xsl:text></p>
                     <p xml:space="preserve"><xsl:text>xxx</xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text>zzz</xsl:text></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><span><xsl:text> zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><span><xsl:text> zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup></span><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text></span><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub></span><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text></span><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
                     <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
                  </div>
               </xsl:document>
            </xsl:variable>
            <xsl:variable name="impl:context" select="$impl:context-doc/node()"/>
            <xsl:apply-templates select="$impl:context"/>
         </xsl:variable>
         <xsl:call-template name="test:report-value">
            <xsl:with-param name="value" select="$x:result"/>
            <xsl:with-param name="wrapper-name" select="'x:result'"/>
            <xsl:with-param name="wrapper-ns" select="'http://www.jenitennison.com/xslt/xspec'"/>
         </xsl:call-template>
         <xsl:call-template name="x:d4e178">
            <xsl:with-param name="x:result" select="$x:result"/>
         </xsl:call-template>
      </x:scenario>
   </xsl:template>
   <xsl:template name="x:d4e178">
      <xsl:param name="x:result" required="yes"/>
      <xsl:message>the result should be as expected</xsl:message>
      <xsl:variable name="impl:expected-doc" as="document-node()">
         <xsl:document>
            <div>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx</xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx</xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx</xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx</xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> zzz</xsl:text></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><span><xsl:text> zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><span><xsl:text> zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text></span><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text></span><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text></span><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text></span><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sup><xsl:text>yyy</xsl:text></sup><xsl:text> </xsl:text></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
               <p xml:space="preserve"><xsl:text>xxx </xsl:text><span><sub><xsl:text>yyy</xsl:text></sub><xsl:text> </xsl:text></span><xsl:text> </xsl:text><span><xsl:text>zzz</xsl:text></span></p>
            </div>
         </xsl:document>
      </xsl:variable>
      <xsl:variable name="impl:expected" select="$impl:expected-doc/node()"/>
      <xsl:variable name="impl:successful"
                    as="xs:boolean"
                    select="test:deep-equal($impl:expected, $x:result, 2)"/>
      <xsl:if test="not($impl:successful)">
         <xsl:message>      FAILED</xsl:message>
      </xsl:if>
      <x:test successful="{$impl:successful}">
         <x:label>the result should be as expected</x:label>
         <xsl:call-template name="test:report-value">
            <xsl:with-param name="value" select="$impl:expected"/>
            <xsl:with-param name="wrapper-name" select="'x:expect'"/>
            <xsl:with-param name="wrapper-ns" select="'http://www.jenitennison.com/xslt/xspec'"/>
         </xsl:call-template>
      </x:test>
   </xsl:template>
</xsl:stylesheet>
